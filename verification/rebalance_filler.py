"""Fixes the "filler tail" pattern found 2026-09-15 (see learning_log /
project memory): cannon damage in sim/engine.py is permanent and cumulative
(a merge never decays), but pirate HP in the real 500 levels does not scale
up to match. Measured on the 180 real `champion_win` levels: pirates in a
level's LAST third die in one hit 76.6% of the time (avg 1.28 shots to kill),
vs 7.2% (avg 3.50 shots) in the first third — once the player survives the
level's one hard spike (usually near the start), everything after it is a
formality regardless of its nominal HP.

This is the mirror image of verification/repair_impossible.py: that tool
shaves HP off a broken level to make it winnable; this one RAISES HP on
already-winnable levels' trivial back-half pirates, to put tension back into
the second half, and re-checks with the SAME champion+solver signal the
audit already trusts. No LLM call anywhere in this file — every accepted
edit is proven by simulation.

Method, per level (only levels currently `champion_win` or
`solved_by_search_only` in the latest weekly audit are candidates — a level
still `no_win_found` is out of scope for this tool, see repair_impossible.py):

1. Play the level once with the champion to find "trivial" pirates: spawned
   in the back half (fila_index >= n_waves // 2) that die to exactly one
   shot. A level with zero trivial pirates is already well-paced — skipped
   entirely, left untouched, logged with a reason.
2. For each trivial pirate, in fila order, try raising its HP (capped at the
   real game's max of 10 — see sim/level.py's Cuadro docstring) by, in this
   order: the cannon damage the pirate actually faced (so it now needs 2
   shots instead of 1), then +1 as a smaller fallback. Each attempt is
   re-simulated (champion + solver escalation, same as the weekly audit) on
   top of whatever earlier edits in this level already stuck — the first
   amount that keeps the level winnable (not `no_win_found`) is kept; if
   neither amount survives, that one pirate is left alone and the next
   candidate is tried. This can never turn a winnable level unwinnable,
   because every edit is verified before being kept.
3. A level with at least one accepted edit is "reworked"; one with trivial
   pirates found but every edit attempt failing to stay winnable is left
   alone too (rare — would mean the level has zero slack anywhere) — this is
   exactly why some of the 500 end up touched and some don't, on purpose,
   not an oversight.

Resumable across ephemeral GitHub Actions runners, same pattern as
repair_impossible.py: progress written to reports/rebalance/results.json and
committed periodically.

Run standalone: `python -m verification.rebalance_filler` (reads
reports/level_audit/latest.json for the champion_win/solved_by_search_only
set — run verification/level_audit.py first if that file doesn't exist).
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config
import git_sync
from policy.loader import load_policy_from_file
from sim.engine import GameEngine
from sim.level import Level
from verification.level_audit import LevelReport, audit_level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

REPORT_DIR = config.ROOT / "reports" / "rebalance"
RESULTS_PATH = REPORT_DIR / "results.json"
LATEST_AUDIT_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"
CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"

MAX_HP = 10  # sim/level.py Cuadro docstring: real game caps hp at 10

TIME_BUDGET_SECONDS = int(os.environ.get("REBALANCE_TIME_BUDGET_SECONDS", 1200))
COMMIT_EVERY_SECONDS = 300


@dataclass
class HpEdit:
    fila_index: int
    cuadro_index: int
    old_hp: int
    new_hp: int


@dataclass
class RebalanceResult:
    levelNumber: int
    password: str
    isHard: bool
    original_classification: str
    original_difficulty_score: float
    trivial_candidates_found: int
    reworked: bool
    edits: list[dict] = field(default_factory=list)
    new_classification: str | None = None
    new_difficulty_score: float | None = None


def _find_trivial_candidates(level: Level, champion) -> list[tuple[int, int, int]]:
    """(fila_index, cuadro_index, cannon_damage_faced) for every pirate that
    spawned in the back half of the level and died to exactly one shot when
    played by `champion` — see module docstring for why "back half" and "one
    shot" are the trivialization signal."""
    n_waves = len(level.filas)
    if n_waves < 4:
        return []  # too short for "first half hard, second half filler" to mean anything

    engine = GameEngine(level)
    meta: dict[int, dict] = {}
    for p in engine.pirates:
        meta[id(p)] = {"fila_index": 0, "column": p.column, "shots": 0, "dmg_faced": 0}

    rounds = 0
    while not engine.is_over() and rounds < DEFAULT_MAX_ROUNDS:
        action = champion.choose_action(engine)
        engine.apply_action(action)
        rounds += 1
        pre_hp = {id(p): p.hp for p in engine.pirates}
        pre_dmg = {c: cannon.damage for c, cannon in engine.cannons.items()}
        engine._shoot_phase()
        for pid, hp_before in pre_hp.items():
            m = meta.get(pid)
            if m is None:
                continue
            hp_after = next((p.hp for p in engine.pirates if id(p) == pid), 0)
            if hp_after != hp_before:
                m["shots"] += 1
                m["dmg_faced"] = pre_dmg.get(m["column"], 1)
        if not engine.is_over():
            engine._next_round()
            for p in engine.pirates:
                if id(p) not in meta:
                    meta[id(p)] = {"fila_index": rounds, "column": p.column, "shots": 0, "dmg_faced": 0}

    if not engine.won:
        return []  # champion doesn't actually win this one today; out of scope here

    half = n_waves // 2
    trivial = [m for m in meta.values() if m["fila_index"] >= half and m["shots"] == 1]

    # Map each trivial (fila_index, column) back to a concrete cuadro in the level.
    out = []
    for t in trivial:
        fi, col = t["fila_index"], t["column"]
        if fi >= len(level.filas):
            continue
        for ci, cuadro in enumerate(level.filas[fi].cuadros):
            if cuadro.index == col and cuadro.tipo >= 1:
                out.append((fi, ci, max(t["dmg_faced"], 1)))
                break
    return out


def _try_bump(level: Level, fi: int, ci: int, amount: int) -> Level | None:
    old_hp = level.filas[fi].cuadros[ci].hp
    new_hp = min(MAX_HP, old_hp + amount)
    if new_hp <= old_hp:
        return None
    variant = copy.deepcopy(level)
    variant.filas[fi].cuadros[ci].hp = new_hp
    return variant


def rebalance_level(
    level: Level, champion, widths: tuple[int, ...], max_rounds: int,
    original_classification: str, original_score: float,
) -> RebalanceResult:
    result = RebalanceResult(
        levelNumber=level.levelNumber,
        password=level.password,
        isHard=level.isHard,
        original_classification=original_classification,
        original_difficulty_score=original_score,
        trivial_candidates_found=0,
        reworked=False,
    )

    candidates = _find_trivial_candidates(level, champion)
    result.trivial_candidates_found = len(candidates)
    if not candidates:
        return result

    working = level
    last_report: LevelReport | None = None
    for fi, ci, dmg_faced in sorted(candidates):
        old_hp = working.filas[fi].cuadros[ci].hp
        accepted = None
        for amount in dict.fromkeys([dmg_faced, 1]):  # try the bigger bump first, dedup with +1 fallback
            variant = _try_bump(working, fi, ci, amount)
            if variant is None:
                continue
            report = audit_level(variant, champion, widths, max_rounds)
            if report.classification != "no_win_found":
                accepted = (variant, report, working.filas[fi].cuadros[ci].hp + amount)
                break
        if accepted is not None:
            variant, report, new_hp = accepted
            working = variant
            last_report = report
            result.edits.append(asdict(HpEdit(fi, ci, old_hp, min(MAX_HP, new_hp))))

    if result.edits:
        result.reworked = True
        result.new_classification = last_report.classification
        result.new_difficulty_score = last_report.difficulty_score

    return result


def _load_results() -> dict[int, dict]:
    if RESULTS_PATH.exists():
        return {int(k): v for k, v in json.loads(RESULTS_PATH.read_text(encoding="utf-8")).items()}
    return {}


def _save_results(results: dict[int, dict]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps({str(k): v for k, v in sorted(results.items())}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> None:
    if not LATEST_AUDIT_PATH.exists():
        raise FileNotFoundError(
            f"{LATEST_AUDIT_PATH} not found — run `python -m verification.level_audit` first."
        )
    audit = json.loads(LATEST_AUDIT_PATH.read_text(encoding="utf-8"))
    widths = tuple(audit.get("escalation_beam_widths", ESCALATION_BEAM_WIDTHS))
    targets = {
        lvl["levelNumber"]: (lvl["classification"], lvl["difficulty_score"])
        for lvl in audit["levels"]
        if lvl["classification"] in ("champion_win", "solved_by_search_only")
    }

    results = _load_results()
    pending = [n for n in targets if n not in results]
    if not pending:
        reworked = sum(1 for r in results.values() if r["reworked"])
        print(f"Nothing pending: {len(targets)} winnable levels, all already attempted "
              f"({reworked} reworked so far).")
        return

    levels_by_number = {
        lv.levelNumber: lv for lv in load_all(CANNONS_LEVELS_DIR) if lv.levelNumber in pending
    }
    champion = load_policy_from_file(config.ROOT / "policy" / "current.py")

    t0 = time.time()
    last_commit = t0
    processed = 0
    for number in pending:
        if time.time() - t0 > TIME_BUDGET_SECONDS:
            print(f"Time budget reached after {processed} levels this run — resuming next run.")
            break

        level = levels_by_number[number]
        orig_class, orig_score = targets[number]
        result = rebalance_level(level, champion, widths, DEFAULT_MAX_ROUNDS, orig_class, orig_score)
        results[number] = asdict(result)
        processed += 1

        if result.reworked:
            status = f"reworked, {len(result.edits)} edits -> {result.new_classification}"
        elif result.trivial_candidates_found:
            status = f"{result.trivial_candidates_found} trivial pirates found, no safe edit — left alone"
        else:
            status = "already balanced, no trivial back-half pirates"
        print(f"level {number}: {status}")

        if time.time() - last_commit > COMMIT_EVERY_SECONDS:
            _save_results(results)
            git_sync.commit_and_push(f"[bot] level rebalance pass - {processed} levels this run, in progress")
            last_commit = time.time()

    _save_results(results)
    reworked = sum(1 for r in results.values() if r["reworked"])
    remaining = len(targets) - len(results)
    git_sync.commit_and_push(
        f"[bot] level rebalance pass - {reworked}/{len(results)} attempted reworked, {remaining} not yet attempted"
    )
    print(f"Rebalance pass done: {reworked}/{len(results)} attempted levels reworked so far ({remaining} not yet attempted).")


if __name__ == "__main__":
    main()

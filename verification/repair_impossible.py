"""Repairs `no_win_found` levels (see verification/level_audit.py) by shaving
exactly 1 HP off exactly one pirate — never removing a pirate outright, never
touching two pirates at once — and re-checking with the SAME champion+solver
signal the weekly audit already trusts (sim/engine.py's real rules,
policy/current.py, verification/solver.py). No LLM call anywhere in this
file: every accepted fix is proven by simulation, not guessed or generated.

Exhaustive per level, on purpose: every pirate with hp>=2 is tried as a
candidate (hp==1 is excluded — an edit there would delete the pirate, which
is out of scope; this tool only ever shaves HP). There's no attempt to
pre-guess "which pirate is the real blocker" — an edit on an unrelated
pirate simply won't flip the level's classification, so trying every
eligible pirate and keeping only the edits that actually work can't produce
a false accept, and can't miss a real fix either.

Among all edits that DO fix a level, keeps the one with the highest
resulting difficulty_score (verification/level_audit.py's existing ranking).
That ranking already scores every `solved_by_search_only` result higher than
every `champion_win` result, so this naturally prefers "still too hard for
the trained policy alone, but has one proven winning line" over "now the AI
just wins it outright" — matching the explicit ask to avoid trivializing a
level while still making it winnable.

If no single-pirate, single-HP edit fixes a level, it's left alone. This
tool never removes a pirate, never edits two pirates in the same attempt,
and never forces a result — an unrepaired level just stays out of
`results.json`'s "fixed" set for a human to look at directly.

Resumable across ephemeral GitHub Actions runners (same pattern as
run_learning_cycle.py's JOB_TIME_BUDGET_SECONDS): progress is written to
reports/repair/results.json and committed periodically, so a run that hits
its time budget or gets killed by the workflow timeout picks up next time
instead of re-doing already-attempted levels.

Run standalone: `python -m verification.repair_impossible` (reads the most
recent reports/level_audit/latest.json for the set of no_win_found levels —
run verification/level_audit.py first if that file doesn't exist yet).
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import config
import git_sync
from policy.loader import load_policy_from_file
from sim.level import Level
from verification.level_audit import LevelReport, audit_level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

REPORT_DIR = config.ROOT / "reports" / "repair"
RESULTS_PATH = REPORT_DIR / "results.json"
LATEST_AUDIT_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"
CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"

# Wall-clock budget PER INVOCATION, not per level — same purpose as
# run_learning_cycle.py's JOB_TIME_BUDGET_SECONDS. An ephemeral CI runner
# gets killed at the workflow's timeout-minutes regardless of what this
# script does, so it checkpoints on its own schedule and exits cleanly with
# time to spare for a final push, rather than losing whatever a hard kill
# interrupts mid-level. Default here is generous for local/manual runs;
# the workflow sets it explicitly.
TIME_BUDGET_SECONDS = int(os.environ.get("REPAIR_TIME_BUDGET_SECONDS", 1200))

# How often (in seconds of wall clock) to commit progress mid-run. Keeps the
# commit count sane (not one commit per level — 301 levels would be 301
# commits) while still bounding how much work a hard kill could lose.
COMMIT_EVERY_SECONDS = 300


@dataclass
class RepairResult:
    levelNumber: int
    password: str
    isHard: bool
    original_difficulty_score: float
    fixed: bool
    fila_index: int | None = None
    cuadro_index: int | None = None
    old_hp: int | None = None
    new_hp: int | None = None
    candidates_tried: int = 0
    new_classification: str | None = None
    new_difficulty_score: float | None = None


def _candidates(level: Level) -> list[tuple[int, int, int]]:
    """(fila_index, cuadro_index, current_hp) for every pirate with hp>=2.
    hp==1 is excluded on purpose — shaving it would delete the pirate
    entirely, which this tool never does (see module docstring)."""
    out = []
    for fi, fila in enumerate(level.filas):
        for ci, cuadro in enumerate(fila.cuadros):
            if cuadro.tipo >= 1 and cuadro.hp >= 2:
                out.append((fi, ci, cuadro.hp))
    return out


def _apply_edit(level: Level, fila_index: int, cuadro_index: int) -> Level:
    variant = copy.deepcopy(level)
    variant.filas[fila_index].cuadros[cuadro_index].hp -= 1
    return variant


def repair_level(
    level: Level, champion, widths: tuple[int, ...], max_rounds: int, original_score: float
) -> RepairResult:
    result = RepairResult(
        levelNumber=level.levelNumber,
        password=level.password,
        isHard=level.isHard,
        original_difficulty_score=original_score,
        fixed=False,
    )
    best_report: LevelReport | None = None
    best_edit: tuple[int, int, int] | None = None

    for fi, ci, old_hp in _candidates(level):
        result.candidates_tried += 1
        variant = _apply_edit(level, fi, ci)
        report = audit_level(variant, champion, widths, max_rounds)
        if report.classification == "no_win_found":
            continue  # this pirate wasn't (the whole of) the real blocker — discard, not a false accept
        if best_report is None or report.difficulty_score > best_report.difficulty_score:
            best_report, best_edit = report, (fi, ci, old_hp)

    if best_report is not None:
        fi, ci, old_hp = best_edit
        result.fixed = True
        result.fila_index, result.cuadro_index = fi, ci
        result.old_hp, result.new_hp = old_hp, old_hp - 1
        result.new_classification = best_report.classification
        result.new_difficulty_score = best_report.difficulty_score

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
        lvl["levelNumber"]: lvl["difficulty_score"]
        for lvl in audit["levels"]
        if lvl["classification"] == "no_win_found"
    }

    results = _load_results()
    pending = [n for n in targets if n not in results]
    if not pending:
        print(f"Nothing pending: {len(targets)} no_win_found levels, all already attempted "
              f"({sum(1 for r in results.values() if r['fixed'])} fixed so far).")
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
        result = repair_level(level, champion, widths, DEFAULT_MAX_ROUNDS, targets[number])
        results[number] = asdict(result)
        processed += 1

        status = f"fixed -> {result.new_classification} (score {result.new_difficulty_score})" \
            if result.fixed else "no single-HP edit works"
        print(f"level {number} ({result.candidates_tried} candidates): {status}")

        if time.time() - last_commit > COMMIT_EVERY_SECONDS:
            _save_results(results)
            git_sync.commit_and_push(f"[bot] level repair pass - {processed} levels this run, in progress")
            last_commit = time.time()

    _save_results(results)
    fixed = sum(1 for r in results.values() if r["fixed"])
    remaining = len(targets) - len(results)
    git_sync.commit_and_push(
        f"[bot] level repair pass - {fixed}/{len(results)} attempted fixed, {remaining} not yet attempted"
    )
    print(f"Repair pass done: {fixed}/{len(results)} attempted levels fixed so far ({remaining} not yet attempted).")


if __name__ == "__main__":
    main()

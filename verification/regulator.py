"""The level regulator bot (2026-09-24) — reworks ALL levels (every
Assets/Levels asset + GeneratedLevels/rejected), one by one, so the whole
campaign becomes playable, tense and varied instead of a curated 200.

Asked for by the user after Level 508: "que regule... que sí haya niveles de
esa forma pero no el 90%, que sea variado, que vaya uno por uno". Per level:

  1. already winnable AND passes the pacing gate (verification/pacing.py)
     -> kept untouched ("kept_ok");
  2. one of the easiest short simple levels, up to BREATHER_SHARE of the
     campaign -> kept as a deliberate breather ("breather") — simple levels
     are allowed, just not as the majority;
  3. otherwise -> local search: random small edits (verification/
     level_mutations.py), each one SIMULATED (champion policy, then the beam
     solver if the champion loses) and pacing-checked; an edit is kept only
     if it improves the fitness below. Broken levels become winnable
     ("repaired"), winnable-but-boring ones get tension back ("regulated").

No LLM anywhere: every accepted level is proven winnable by simulation.

Runs sharded in GitHub Actions (.github/workflows/level_regulation.yml):
shard k of N handles levels whose position in the sorted list % N == k and
writes reports/regulation/<pass>/shard_<k>.json, committing every few
minutes. Resumable: levels already in the shard file are skipped, so a
killed/timed-out run just continues next time. Nothing here touches the
Cannons repo — verification/apply_regulation.py applies the results.

Run locally: `python -m verification.regulator --shard 0 --shards 1 --limit 5`
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
import git_sync
from policy.loader import load_policy_from_file
from sim.level import Level, empty_fila_indices
from verification import pacing
from verification.level_audit import audit_level
from verification.level_mutations import OPERATORS, normalize
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS

REPORT_ROOT = config.ROOT / "reports" / "regulation"
LATEST_AUDIT_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"
REJECTED_DIR = config.CANNONS_REPO / "GeneratedLevels" / "rejected"

BREATHER_SHARE = 0.15       # simple levels kept on purpose, as a share of all levels
BREATHER_MAX_FILAS = 8
SEARCH_WIDTHS = (200, 800)  # a win found at any width is a real win; 3000 only adds time
MAX_EVALS_PER_LEVEL = 400  # evals are ~0.1-0.3s; broken levels need many small steps
MAX_SECONDS_PER_LEVEL = 300
STALL_AFTER_GOAL = 25       # once winnable + pacing ok, stop after this many non-improving tries
STALL_CHASING_ARCHETYPE = 70  # ... unless the target archetype isn't reached yet
REPAIR_TARGET_DIFFICULTY = 16.0     # upper champion_win range (release p90 ~16)
SEARCH_ONLY_EFFECTIVE_SCORE = 22.0  # solved_by_search_only ranks above every champion_win (max ~20)
ARCHETYPE_BONUS = 30.0              # fitness reward for hitting the level's target archetype
# edits that push a level toward each archetype (see verification/pacing.ARCHETYPES)
ARCHETYPE_OPERATORS = {
    "swarm": (("split", 3), ("widen_single", 2), ("shave", 1)),
    "tank": (("grow", 3), ("bump_late", 1), ("remove", 1)),
    "wall": (("stack", 3), ("move_column", 1)),
    "crescendo": (("add_late", 3), ("bump_late", 1), ("shift_later", 1)),
    "burst": (("shift_later", 3), ("merge_filas", 2), ("remove", 1)),
    "switch": (("move_column", 3), ("shift_later", 1)),
}
TIME_BUDGET_SECONDS = int(os.environ.get("REGULATION_TIME_BUDGET_SECONDS", 1200))
CHECKPOINT_EVERY_SECONDS = 600


@dataclass
class Evaluation:
    classification: str   # champion_win | solved_by_search_only | no_win_found
    difficulty: float     # comparable scale, see _effective_difficulty
    pacing: dict
    pirates_left: int = 0  # broken levels only: pirates alive when the solver lost (closeness)
    primary: str | None = None  # main archetype (verification/pacing.primary_archetype)

    @property
    def winnable(self) -> bool:
        return self.classification != "no_win_found"

    @property
    def pacing_ok(self) -> bool:
        return not self.pacing["problems"]


def _effective_difficulty(classification: str, score: float) -> float:
    if classification == "champion_win":
        return score
    return SEARCH_ONLY_EFFECTIVE_SCORE if classification == "solved_by_search_only" else 0.0


class Evaluator:
    """Simulation + pacing for one level, cached by shape (the same edit is
    often proposed twice)."""

    def __init__(self, champion):
        self.champion = champion
        self.cache: dict[str, Evaluation] = {}

    def __call__(self, level: Level) -> Evaluation:
        key = level.shape_signature()
        if key not in self.cache:
            report = audit_level(level, self.champion, SEARCH_WIDTHS, DEFAULT_MAX_ROUNDS)
            self.cache[key] = Evaluation(
                classification=report.classification,
                difficulty=_effective_difficulty(report.classification, report.difficulty_score),
                pacing=asdict(pacing.pacing_report(level)),
                pirates_left=report.solver_pirates_remaining or 0,
                primary=pacing.primary_archetype(level),
            )
        return self.cache[key]


def _pacing_penalty(p: dict) -> float:
    return (100 * max(0.0, pacing.MIN_LATE_DEMAND - p["late_demand"])
            + 100 * max(0.0, p["single_pirate_share"] - pacing.MAX_SINGLE_PIRATE_SHARE)
            + 30 * max(0, p["filas"] - pacing.MAX_FILAS))  # 10 left Level 508 at 19 rounds


def fitness(ev: Evaluation, target_difficulty: float, target_archetype: str | None = None) -> float:
    """Lexicographic in practice: winnable first, then pacing, then closeness
    to `target_difficulty` (a winnable level keeps its own difficulty, a broken
    one aims at REPAIR_TARGET_DIFFICULTY — hard, but beatable by the champion,
    i.e. by a decent human, not only by the brute-force solver)."""
    if not ev.winnable:
        # gradient toward winnable (fewer survivors when the solver loses);
        # pacing only breaks ties here — letting it pull harder made the search
        # ADD pirates to broken levels and get stuck at 2-3 survivors
        return -15 * ev.pirates_left - 0.1 * _pacing_penalty(ev.pacing)
    bonus = ARCHETYPE_BONUS if target_archetype and ev.primary == target_archetype else 0.0
    return 1000.0 - _pacing_penalty(ev.pacing) - 3 * abs(ev.difficulty - target_difficulty) + bonus


def _operator_weights(ev: Evaluation, target_archetype: str | None) -> dict[str, float]:
    """Which edits to try, by what is wrong right now."""
    p = ev.pacing
    w = {name: 0.3 for name in OPERATORS}  # always a little of everything
    if ev.winnable and target_archetype and ev.primary != target_archetype:
        for name, x in ARCHETYPE_OPERATORS[target_archetype]:
            w[name] += x
    if not ev.winnable:
        for name, x in (("shave", 4), ("remove", 3), ("move_column", 2), ("shift_later", 1), ("split", 1)):
            w[name] += x
    if p["late_demand"] < pacing.MIN_LATE_DEMAND:
        for name, x in (("add_late", 4), ("split", 2), ("bump_late", 2), ("shift_later", 1)):
            w[name] += x
    if p["single_pirate_share"] > pacing.MAX_SINGLE_PIRATE_SHARE:
        for name, x in (("widen_single", 4), ("split", 2), ("merge_filas", 1)):
            w[name] += x
    if p["filas"] > pacing.MAX_FILAS:
        for name, x in (("merge_filas", 5), ("drop_fila", 3)):
            w[name] += x
    return w


def regulate_level(level: Level, evaluate: Evaluator, target_difficulty: float,
                   target_archetype: str | None, rng: random.Random) -> tuple[Level, Evaluation, int]:
    """Hill climb from `level`. Returns (best level, its evaluation, evals used)."""
    best = normalize(level)
    best_ev = evaluate(best)
    best_fit = fitness(best_ev, target_difficulty, target_archetype)
    started, evals, stall = time.monotonic(), 0, 0
    while evals < MAX_EVALS_PER_LEVEL and time.monotonic() - started < MAX_SECONDS_PER_LEVEL:
        if best_ev.winnable and best_ev.pacing_ok:
            # the archetype is a preference, not a requirement: search a bit
            # longer for it, then stop anyway
            reached = target_archetype is None or best_ev.primary == target_archetype
            if stall >= (STALL_AFTER_GOAL if reached else STALL_CHASING_ARCHETYPE):
                break
        weights = _operator_weights(best_ev, target_archetype)
        name = rng.choices(list(weights), weights=list(weights.values()))[0]
        candidate = OPERATORS[name](best, rng)
        if candidate is None:
            stall += 1
            continue
        evals += 1
        ev = evaluate(candidate)
        fit = fitness(ev, target_difficulty, target_archetype)
        # while broken, also accept sideways moves (equal fitness) to walk off
        # plateaus; once winnable, only strict improvements
        if fit > best_fit or (not best_ev.winnable and fit == best_fit):
            best, best_ev, best_fit, stall = candidate, ev, fit, 0
        else:
            stall += 1
    return best, best_ev, evals


# ---- campaign-level inputs -------------------------------------------------

def load_all_levels() -> list[Level]:
    """Every level asset + reviewed-and-rejected bot drops (e.g. 508), sorted
    by levelNumber. Rejected JSON may break the ranges (508 has hp 12);
    `normalize` fixes that before any simulation."""
    levels = {lv.levelNumber: lv for lv in load_all(config.CANNONS_REPO / "Assets" / "Levels")}
    for path in sorted(REJECTED_DIR.glob("*.json")) if REJECTED_DIR.exists() else []:
        lv = Level.load(path)
        levels.setdefault(lv.levelNumber, lv)
    return [levels[n] for n in sorted(levels)]


def load_audit() -> dict[int, dict]:
    if not LATEST_AUDIT_PATH.exists():
        return {}
    data = json.loads(LATEST_AUDIT_PATH.read_text(encoding="utf-8"))
    return {r["levelNumber"]: r for r in data["levels"]}


def pick_breathers(levels: list[Level], audit: dict[int, dict]) -> set[int]:
    """The easiest short levels that are winnable but fail pacing, up to
    BREATHER_SHARE of the campaign. Deterministic, so every shard agrees."""
    candidates = []
    for lv in levels:
        a = audit.get(lv.levelNumber)
        if not a or a["classification"] == "no_win_found" or len(lv.filas) > BREATHER_MAX_FILAS:
            continue
        if pacing.pacing_report(lv).ok:
            continue
        candidates.append((a["difficulty_score"], lv.levelNumber))
    quota = int(BREATHER_SHARE * len(levels))
    return {n for _, n in sorted(candidates)[:quota]}


def assign_target_archetypes(levels: list[Level], audit: dict[int, dict], breathers: set[int]) -> dict[int, str]:
    """Target archetype for every level that will be reworked, chosen so the
    WHOLE campaign ends up balanced: start from the archetypes of the levels
    expected to stay as they are (winnable in the audit + pacing ok, and the
    breathers), then give each reworked level, in levelNumber order, the
    archetype that is rarest at that point. Deterministic, so every shard
    computes the same plan without talking to the others."""
    counts = {a: 0 for a in pacing.ARCHETYPES}
    rework = []
    for lv in levels:
        a = audit.get(lv.levelNumber)
        stays = (a is not None and a["classification"] != "no_win_found"
                 and not lv.structure_errors() and not empty_fila_indices(lv)
                 and (pacing.pacing_report(lv).ok or lv.levelNumber in breathers))
        if stays:
            primary = pacing.primary_archetype(lv)
            if primary:
                counts[primary] += 1
        else:
            rework.append(lv.levelNumber)
    targets = {}
    for number in rework:
        target = min(counts, key=lambda arch: (counts[arch], list(pacing.ARCHETYPES).index(arch)))
        targets[number] = target
        counts[target] += 1
    return targets


# ---- per-level decision + result record ----------------------------------

def process_level(level: Level, is_breather: bool, target_archetype: str | None, evaluate: Evaluator) -> dict:
    started = time.monotonic()
    original = normalize(level)
    before = evaluate(original)
    record = {"levelNumber": level.levelNumber, "before": asdict(before)}
    # untouched only if normalize changed nothing that matters (tipo is cosmetic,
    # so compare shape; out-of-range fields like 508's hp 12 force a rework)
    # empty rounds ("vacíos") are a defect too: normalize drops them, which
    # changes the timing, so such a level always goes through the search
    valid_as_is = (not level.structure_errors() and not empty_fila_indices(level)
                   and original.shape_signature() == level.shape_signature())

    if before.winnable and before.pacing_ok and valid_as_is:
        record["status"] = "kept_ok"
    elif is_breather and before.winnable and valid_as_is:
        record["status"] = "breather"
    else:
        target = before.difficulty if before.winnable else REPAIR_TARGET_DIFFICULTY
        rng = random.Random(level.levelNumber)  # reproducible per level
        best, after, evals = regulate_level(original, evaluate, target, target_archetype, rng)
        best.levelNumber, best.password, best.isHard = level.levelNumber, level.password, level.isHard
        record.update({
            "status": _status(before, after),
            "target_archetype": target_archetype,
            "after": asdict(after),
            "evaluations": evals,
            "level": best.to_dict(),
        })
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def _status(before: Evaluation, after: Evaluation) -> str:
    if not after.winnable:
        return "unresolved"
    if not before.winnable:
        return "repaired" if after.pacing_ok else "repaired_partial"
    return "regulated" if after.pacing_ok else "improved_partial"


# ---- sharded, resumable runner --------------------------------------------

def _shard_path(pass_name: str, shard: int):
    return REPORT_ROOT / pass_name / f"shard_{shard}.json"


def _load_results(path) -> dict[int, dict]:
    if not path.exists():
        return {}
    return {r["levelNumber"]: r for r in json.loads(path.read_text(encoding="utf-8"))["levels"]}


def _save_results(path, results: dict[int, dict], total: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [results[n] for n in sorted(results)]
    path.write_text(json.dumps({"assigned": total, "done": len(rows), "levels": rows},
                               indent=1, ensure_ascii=False), encoding="utf-8")


def _checkpoint(message: str, push: bool) -> None:
    """Commit+push with retries: 8 shards push to the same branch, so a push
    can lose the race; git_sync already rebases, it just needs another try."""
    if not push:
        return
    for attempt in range(5):
        if git_sync.commit_and_push(message):
            return
        time.sleep(5 + random.random() * 20)
    print("  checkpoint: push failed 5 times — progress stays on disk, next checkpoint retries")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regulate every level (sharded, resumable).")
    parser.add_argument("--shard", type=int, default=int(os.environ.get("REGULATION_SHARD", 0)))
    parser.add_argument("--shards", type=int, default=int(os.environ.get("REGULATION_SHARDS", 1)))
    parser.add_argument("--pass-name", default=os.environ.get("REGULATION_PASS", "pass1"))
    parser.add_argument("--limit", type=int, default=None, help="process at most N levels (local testing)")
    parser.add_argument("--no-push", action="store_true", help="never commit/push (local testing)")
    args = parser.parse_args(argv)
    push = not args.no_push and os.environ.get("CI") == "true"

    levels = load_all_levels()
    audit = load_audit()
    breathers = pick_breathers(levels, audit)
    targets = assign_target_archetypes(levels, audit, breathers)
    mine = [lv for i, lv in enumerate(levels) if i % args.shards == args.shard]
    path = _shard_path(args.pass_name, args.shard)
    results = _load_results(path)
    todo = [lv for lv in mine if lv.levelNumber not in results][: args.limit]
    print(f"shard {args.shard}/{args.shards} ({args.pass_name}): {len(mine)} levels, "
          f"{len(results)} already done, {len(todo)} to do now; {len(breathers)} breathers campaign-wide")

    evaluate = Evaluator(load_policy_from_file(config.ROOT / "policy" / "current.py"))
    started = last_checkpoint = time.monotonic()
    for lv in todo:
        if time.monotonic() - started > TIME_BUDGET_SECONDS:
            print("  time budget reached — stopping, the next run resumes from here")
            break
        try:
            record = process_level(lv, lv.levelNumber in breathers, targets.get(lv.levelNumber), evaluate)
        except Exception as e:  # noqa: BLE001 — one bad level must not kill hours of work
            import traceback
            traceback.print_exc()
            record = {"levelNumber": lv.levelNumber, "status": "error", "error": repr(e)}
        results[lv.levelNumber] = record
        _save_results(path, results, len(mine))
        print(f"  level {lv.levelNumber}: {record['status']} ({record.get('seconds', 0)}s)", flush=True)
        if time.monotonic() - last_checkpoint > CHECKPOINT_EVERY_SECONDS:
            _checkpoint(f"[bot] regulation {args.pass_name} shard {args.shard}: "
                        f"{len(results)}/{len(mine)}", push)
            last_checkpoint = time.monotonic()

    _checkpoint(f"[bot] regulation {args.pass_name} shard {args.shard}: {len(results)}/{len(mine)}", push)
    print(f"shard {args.shard}: {len(results)}/{len(mine)} done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

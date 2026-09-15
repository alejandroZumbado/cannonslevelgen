"""Fixes "dead rounds" in the 500 real levels: a fila that spawns zero
pirates is a round where the player can act but nothing is at stake — found
2026-09-15 looking at the rebalance ledger's grid view (F1/F2 both empty in
a real level). Measured: 310/500 real levels have at least one (469 total).

This is the "modifier" half of the fix — the level GENERATOR gets the same
invariant applied unconditionally at creation time (see
`sim.level.fill_empty_filas`, wired into production/daily_generator.py and
learning/level_designer.py right before their existing winnability check,
so a freshly generated level can never ship with one). The EXISTING 500 are
different: they're already shipped/known-good, so a blind fill isn't safe —
this file fills one empty fila at a time and VERIFIES with the same
champion+solver signal the weekly audit trusts (verification/level_audit.py)
that the level's classification did not get WORSE before keeping the edit.
"Worse" is champion_win -> solved_by_search_only|no_win_found, or
solved_by_search_only -> no_win_found — never accepted. No LLM call
anywhere in this file, pure simulation, same as repair_impossible.py and
rebalance_filler.py (which this mirrors in structure).

Per empty fila: try `sim.level.with_filled_fila` in column `fila_index % 5`
first, then the other 4 columns in order, keep the FIRST placement whose
resulting classification is not worse than the level's current state (which
already includes any earlier fila in this same level that got fixed). If
none of the 5 columns work for a given fila — should be rare, a single
hp=1 pirate is about as weak an edit as exists — that fila is left empty
and logged, never forced.

Applies to ALL 500 real levels regardless of current classification (this
is a pacing fix, independent of whether repair_impossible.py or
rebalance_filler.py have already touched a level, and independent of
whether either of THEIR edits has been applied to the real .asset files
yet — this tool reads the real files fresh, same as the others).

Resumable across ephemeral GitHub Actions runners, same pattern as the
other two verification/*.py tools: progress written to
reports/fill_empty_rounds/results.json and committed periodically.

Run standalone: `python -m verification.fill_empty_rounds`
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

import config
import git_sync
from policy.loader import load_policy_from_file
from sim.level import Level, empty_fila_indices, with_filled_fila
from verification.level_audit import LevelReport, audit_level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

REPORT_DIR = config.ROOT / "reports" / "fill_empty_rounds"
RESULTS_PATH = REPORT_DIR / "results.json"
LATEST_AUDIT_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"
CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"

NUM_COLUMNS = 5

# "Worse" ordering — never accept an edit that lowers a level's rank.
_RANK = {"no_win_found": 0, "solved_by_search_only": 1, "champion_win": 2}

TIME_BUDGET_SECONDS = int(os.environ.get("FILL_TIME_BUDGET_SECONDS", 1200))
COMMIT_EVERY_SECONDS = 300


@dataclass
class FillResult:
    levelNumber: int
    password: str
    isHard: bool
    original_classification: str
    empty_filas_found: int
    filas_fixed: list[dict] = field(default_factory=list)
    filas_left_empty: list[int] = field(default_factory=list)
    final_classification: str | None = None
    final_difficulty_score: float | None = None
    changed_classification: bool = False


def fix_level(
    level: Level, champion, widths: tuple[int, ...], max_rounds: int, original_classification: str,
) -> FillResult:
    empties = empty_fila_indices(level)
    result = FillResult(
        levelNumber=level.levelNumber,
        password=level.password,
        isHard=level.isHard,
        original_classification=original_classification,
        empty_filas_found=len(empties),
    )
    if not empties:
        return result

    working = level
    current_rank = _RANK[original_classification]
    current_class = original_classification
    current_score: float | None = None

    for fi in empties:
        col_order = [fi % NUM_COLUMNS] + [c for c in range(NUM_COLUMNS) if c != fi % NUM_COLUMNS]
        accepted = None
        for col in col_order:
            variant = with_filled_fila(working, fi, col)
            report = audit_level(variant, champion, widths, max_rounds)
            if _RANK[report.classification] >= current_rank:
                accepted = (variant, report, col)
                break
        if accepted is not None:
            working, report, col = accepted
            current_rank = _RANK[report.classification]
            current_class = report.classification
            current_score = report.difficulty_score
            result.filas_fixed.append({"fila_index": fi, "column": col})
        else:
            result.filas_left_empty.append(fi)

    if result.filas_fixed:
        result.final_classification = current_class
        result.final_difficulty_score = current_score
        result.changed_classification = current_class != original_classification

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
    classifications = {lvl["levelNumber"]: lvl["classification"] for lvl in audit["levels"]}

    results = _load_results()
    pending = [n for n in classifications if n not in results]
    if not pending:
        touched = sum(1 for r in results.values() if r["filas_fixed"])
        print(f"Nothing pending: {len(classifications)} levels, all already attempted "
              f"({touched} had at least one dead round fixed).")
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
        result = fix_level(level, champion, widths, DEFAULT_MAX_ROUNDS, classifications[number])
        results[number] = asdict(result)
        processed += 1

        if result.empty_filas_found == 0:
            status = "no dead rounds"
        elif result.filas_fixed and not result.filas_left_empty:
            status = f"{len(result.filas_fixed)}/{result.empty_filas_found} dead rounds filled"
        elif result.filas_fixed:
            status = f"{len(result.filas_fixed)}/{result.empty_filas_found} filled, {len(result.filas_left_empty)} left empty (no safe column)"
        else:
            status = f"{result.empty_filas_found} dead rounds found, none safe to fill"
        print(f"level {number}: {status}")

        if time.time() - last_commit > COMMIT_EVERY_SECONDS:
            _save_results(results)
            git_sync.commit_and_push(f"[bot] fill empty rounds pass - {processed} levels this run, in progress")
            last_commit = time.time()

    _save_results(results)
    touched = sum(1 for r in results.values() if r["filas_fixed"])
    remaining = len(classifications) - len(results)
    git_sync.commit_and_push(
        f"[bot] fill empty rounds pass - {touched}/{len(results)} attempted had dead rounds fixed, {remaining} not yet attempted"
    )
    print(f"Fill pass done: {touched}/{len(results)} attempted levels had dead rounds fixed ({remaining} not yet attempted).")


if __name__ == "__main__":
    main()

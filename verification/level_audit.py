"""Weekly audit: plays all 500 real Cannons levels (Assets/Levels/*.asset,
loaded via verification/official_levels.py) with two independent signals and
produces a per-level difficulty report.

Why two signals, not one:

1. `champion` — the current learned policy (policy/current.py) exactly as
   the real pipeline trained it. Deterministic, one run per level, instant.
   Answers "can the AI we've actually trained beat this today".
2. `solver` — an independent wide beam search over the real rules in
   sim/engine.py (verification/solver.py), run ONLY on levels the champion
   lost. Answers "is this level winnable by strong play at all", which is
   what actually separates "the policy is too weak here" from "the level
   might be broken". Skipped when the champion already won — a policy win
   is already a definitive "yes, winnable", so re-solving would spend a lot
   of compute to learn nothing new (measured 2026-09-10: this is where
   nearly all the run's cost is — solver calls average single-digit seconds
   at beam_width=800 but the levels needing it are still a large minority).

Classification per level (see LevelReport.classification):
  - "champion_win": the trained AI wins outright today.
  - "solved_by_search_only": the AI loses, but a wide search over the real
    rules finds a win — the level IS winnable, the CURRENT policy just
    hasn't learned how yet. The single most actionable bucket: direct
    evidence of a policy gap, not a level bug.
  - "no_win_found": both the AI and the wide search lose. Strongest evidence
    on hand that the level may be broken/unwinnable — NOT a certified proof
    (beam search is wide, not exhaustive; see verification/solver.py's
    module docstring) — needs a human look before concluding it's a real bug.

Run standalone: `python -m verification.level_audit` (writes a dated JSON
report under reports/level_audit/ and a `latest.json` copy, printing a
week-over-week comparison against whatever `latest.json` said before this
run overwrote it).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import config
import git_sync
from policy.loader import load_policy_from_file
from sim.engine import run_level
from sim.level import Level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS, solve_thoroughly

REPORT_DIR = config.ROOT / "reports" / "level_audit"
LATEST_PATH = REPORT_DIR / "latest.json"
CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"


@dataclass
class LevelReport:
    levelNumber: int
    password: str
    isHard: bool
    classification: str  # champion_win | solved_by_search_only | no_win_found
    champion_won: bool
    champion_rounds_played: int
    champion_max_position_reached: int  # 0..3; 3 only happens on a loss
    solver_ran: bool
    solver_won: bool | None = None
    solver_rounds_played: int | None = None
    solver_beam_width_used: int | None = None
    solver_total_nodes: int | None = None
    solver_pirates_remaining: int | None = None  # only meaningful when solver_won is False
    difficulty_score: float = 0.0  # higher = harder; see _difficulty_score


def _difficulty_score(report: LevelReport, level: Level) -> float:
    """Higher = harder. Purely a ranking aid for the report, not used for
    classification (that's the binary champion/solver outcome above).

    - champion_win: scaled DOWN from a baseline by how much margin the win
      had — fewer rounds relative to the level's own fila count, and never
      coming close to the loss line, both mean "not hard". Level total
      pirate count and max hp scale a level's raw content up, so two levels
      that both win cleanly are still ranked by how much they actually threw
      at the player.
    - solved_by_search_only: always ranked HARDER than every champion_win —
      the trained AI could not do this one, regardless of margin.
    - no_win_found: always the hardest tier, further ranked by how much of
      the level got through anyway (more pirates cleared before the wide
      search still lost = closer to a real win, so lower within this tier)."""
    content_weight = level.total_pirates() + level.max_hp()

    if report.classification == "champion_win":
        base = 10.0
        margin_bonus = -2.0 * (report.champion_rounds_played / max(len(level.filas), 1))
        return round(base + content_weight * 0.3 + margin_bonus, 2)

    if report.classification == "solved_by_search_only":
        base = 100.0
        return round(base + content_weight * 0.5, 2)

    # no_win_found — rank by how close the widest search attempt got (fewer
    # pirates left alive at the end = closer to actually winning = slightly
    # less severe within this tier)
    base = 1000.0
    closeness_bonus = -1.0 * (report.solver_pirates_remaining or 0)
    return round(base + content_weight * 0.5 + closeness_bonus, 2)


def audit_level(level: Level, champion, widths: tuple[int, ...], max_rounds: int) -> LevelReport:
    champion_engine = run_level(level, champion)

    report = LevelReport(
        levelNumber=level.levelNumber,
        password=level.password,
        isHard=level.isHard,
        classification="champion_win" if champion_engine.won else "pending",
        champion_won=champion_engine.won,
        champion_rounds_played=champion_engine.rounds_played,
        champion_max_position_reached=champion_engine.max_position_reached,
        solver_ran=False,
    )

    if champion_engine.won:
        report.difficulty_score = _difficulty_score(report, level)
        return report

    # Champion lost -> always escalate through every search tier before
    # concluding anything (see solve_thoroughly's module docstring) — asked
    # explicitly to always prefer the slower/more-thorough option here.
    solver_result = solve_thoroughly(level, widths=widths, max_rounds=max_rounds)
    report.solver_ran = True
    report.solver_won = solver_result.won
    report.solver_rounds_played = solver_result.rounds_played
    report.solver_beam_width_used = solver_result.beam_width_used
    report.solver_total_nodes = solver_result.total_nodes_all_tiers
    report.solver_pirates_remaining = solver_result.pirates_remaining
    report.classification = "solved_by_search_only" if solver_result.won else "no_win_found"
    report.difficulty_score = _difficulty_score(report, level)
    return report


def run_audit(widths: tuple[int, ...] = ESCALATION_BEAM_WIDTHS, max_rounds: int = DEFAULT_MAX_ROUNDS) -> dict:
    levels = load_all(CANNONS_LEVELS_DIR)
    champion = load_policy_from_file(config.ROOT / "policy" / "current.py")
    champion_source = (config.ROOT / "policy" / "current.py").read_text(encoding="utf-8")

    t0 = time.time()
    reports = [audit_level(level, champion, widths, max_rounds) for level in levels]
    elapsed = time.time() - t0

    counts = {"champion_win": 0, "solved_by_search_only": 0, "no_win_found": 0}
    for r in reports:
        counts[r.classification] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "policy_name": getattr(champion, "name", "unknown"),
        "policy_source_chars": len(champion_source),
        "escalation_beam_widths": list(widths),
        "max_rounds": max_rounds,
        "total_levels": len(levels),
        "elapsed_seconds": round(elapsed, 1),
        "counts": counts,
        "levels": [asdict(r) for r in sorted(reports, key=lambda r: r.difficulty_score, reverse=True)],
    }


def _compare(previous: dict | None, current: dict) -> dict:
    if previous is None:
        return {"has_previous": False}

    prev_by_num = {lvl["levelNumber"]: lvl for lvl in previous["levels"]}
    newly_solved = []
    newly_broken = []
    for lvl in current["levels"]:
        prev = prev_by_num.get(lvl["levelNumber"])
        if prev is None:
            continue
        if prev["classification"] != "champion_win" and lvl["classification"] == "champion_win":
            newly_solved.append(lvl["levelNumber"])
        if prev["classification"] == "champion_win" and lvl["classification"] != "champion_win":
            newly_broken.append(lvl["levelNumber"])

    return {
        "has_previous": True,
        "previous_generated_at": previous["generated_at"],
        "previous_counts": previous["counts"],
        "current_counts": current["counts"],
        "newly_champion_win": sorted(newly_solved),
        "regressed_from_champion_win": sorted(newly_broken),
    }


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    previous = json.loads(LATEST_PATH.read_text(encoding="utf-8")) if LATEST_PATH.exists() else None

    report = run_audit()
    comparison = _compare(previous, report)
    report["comparison_to_previous_run"] = comparison

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dated_path = REPORT_DIR / f"{stamp}.json"
    dated_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    LATEST_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Audit complete in {report['elapsed_seconds']}s: {report['counts']}")
    if comparison["has_previous"]:
        print(f"vs {comparison['previous_generated_at']}: "
              f"newly champion_win={comparison['newly_champion_win']}, "
              f"regressed={comparison['regressed_from_champion_win']}")
    else:
        print("No previous report to compare against (first run).")

    stamp_msg = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    git_sync.commit_and_push(
        f"[bot] weekly level audit - {stamp_msg} - "
        f"{report['counts']['champion_win']}/{report['total_levels']} champion wins"
    )


if __name__ == "__main__":
    main()

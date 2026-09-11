"""One-off (not scheduled) counterpart to verification/level_audit.py: runs
the exact same 500-level audit — same levels, same solver, same
classification and scoring — but with policy/baseline.py's naive v0 policy
instead of the trained champion.

Purpose: policy/current.py's win_rate against the synthetic training
benchmark (see knowledge/strategy_history.json) only tells you the trained
policy beats ITS PAST SELVES. It says nothing about how much of that is
actually attributable to a month of self-play learning versus how well an
untrained heuristic would have done anyway. Running baseline_v0 through the
identical 500-real-level audit gives a real before/after number.

Run standalone: `python -m verification.baseline_audit` (writes
reports/level_audit/baseline_v0.json, does not touch latest.json/history —
that trend is champion-only — and does not run on a schedule, since
baseline.py is deliberately never touched by hand, see its own docstring).
"""
from __future__ import annotations

import json
from pathlib import Path

import config
import git_sync
from policy.baseline import BaselinePolicy
from verification.level_audit import REPORT_DIR, run_audit

OUTPUT_PATH = REPORT_DIR / "baseline_v0.json"


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    source_len = len((config.ROOT / "policy" / "baseline.py").read_text(encoding="utf-8"))
    report = run_audit(policy=BaselinePolicy(), policy_source_chars=source_len)

    OUTPUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Baseline audit complete in {report['elapsed_seconds']}s: {report['counts']}")

    git_sync.commit_and_push(
        f"[bot] baseline_v0 audit (one-off, pre-learning comparison) - "
        f"{report['counts']['champion_win']}/{report['total_levels']} wins"
    )


if __name__ == "__main__":
    main()

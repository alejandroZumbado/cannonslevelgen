#!/usr/bin/env python
"""Entry point for the month-1 learning phase. Runs on a GitHub Actions
schedule (.github/workflows/learning.yml), NOT on your own machine — each
invocation gets a fresh, disposable VM, runs one strategy cycle + one
level-design cycle, commits+pushes whatever it learned back to this repo
(git_sync.py) so the next run — on a completely different disposable VM —
picks up where this one left off. Also fine to run locally for testing; it
still pushes, same as any other command in this repo would.

The daily token budget (llm/budget.py) is tracked in state/budget.json,
which IS committed (see .gitignore) specifically so it survives across
these disposable runners — without that commit, every run would think it
had a fresh 180k-token day and the free-tier cap would mean nothing.

Usage: python run_learning_cycle.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime

# generated text (hypotheses, reasoning) can contain characters outside the
# default console codepage — this runs unattended, it must never crash on a
# cosmetic print.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from llm.budget import BudgetExceeded, remaining_tokens, calls_made_today
from learning import strategy_learner, level_designer
import git_sync


def main() -> int:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] learning cycle starting "
          f"(budget remaining today: {remaining_tokens()} tokens, {calls_made_today()} calls made)")

    budget_exhausted = False

    try:
        result = strategy_learner.run_cycle()
        print(f"  strategy_learner: {result}")
    except BudgetExceeded as e:
        print(f"  strategy_learner: budget exhausted for today ({e}) — stopping cleanly.")
        budget_exhausted = True
    except Exception:  # noqa: BLE001
        print("  strategy_learner: unexpected error, see traceback below")
        traceback.print_exc()

    if not budget_exhausted:
        try:
            result = level_designer.run_cycle()
            print(f"  level_designer: {result}")
        except BudgetExceeded as e:
            print(f"  level_designer: budget exhausted for today ({e}) — stopping cleanly.")
        except Exception:  # noqa: BLE001
            print("  level_designer: unexpected error, see traceback below")
            traceback.print_exc()

    print(f"[{datetime.now().isoformat(timespec='seconds')}] cycle done "
          f"(budget remaining: {remaining_tokens()} tokens)")

    try:
        git_sync.sync_cycle_results()
    except Exception:  # noqa: BLE001 — a sync failure shouldn't mask the cycle's own result
        print("  git_sync: failed, see traceback below (this run's results may be lost)")
        traceback.print_exc()

    return 0


if __name__ == "__main__":
    sys.exit(main())

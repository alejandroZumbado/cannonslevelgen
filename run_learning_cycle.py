#!/usr/bin/env python
"""Entry point for the month-1 learning phase. Meant to be invoked repeatedly
(every 5-15 min, e.g. via Windows Task Scheduler — see scripts/) across the
whole day; each invocation runs one strategy cycle + one level-design cycle
and then exits. The daily token budget (llm/budget.py) makes repeated
invocations safe: once the day's budget is spent, cycles no-op until the next
calendar day instead of erroring out or overspending.

Usage: python run_learning_cycle.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime

# generated text (hypotheses, reasoning) can contain characters outside the
# Windows console's default codepage — this runs unattended under Task
# Scheduler for a month, it must never crash on a cosmetic print.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from llm.budget import BudgetExceeded, remaining_tokens, calls_made_today
from learning import strategy_learner, level_designer


def main() -> int:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] learning cycle starting "
          f"(budget remaining today: {remaining_tokens()} tokens, {calls_made_today()} calls made)")

    try:
        result = strategy_learner.run_cycle()
        print(f"  strategy_learner: {result}")
    except BudgetExceeded as e:
        print(f"  strategy_learner: budget exhausted for today ({e}) — stopping cleanly.")
        return 0
    except Exception:  # noqa: BLE001
        print("  strategy_learner: unexpected error, see traceback below")
        traceback.print_exc()

    try:
        result = level_designer.run_cycle()
        print(f"  level_designer: {result}")
    except BudgetExceeded as e:
        print(f"  level_designer: budget exhausted for today ({e}) — stopping cleanly.")
        return 0
    except Exception:  # noqa: BLE001
        print("  level_designer: unexpected error, see traceback below")
        traceback.print_exc()

    print(f"[{datetime.now().isoformat(timespec='seconds')}] cycle done "
          f"(budget remaining: {remaining_tokens()} tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

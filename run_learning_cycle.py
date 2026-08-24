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

import json
import sys
import traceback
from datetime import datetime

# generated text (hypotheses, reasoning) can contain characters outside the
# default console codepage — this runs unattended, it must never crash on a
# cosmetic print.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from llm.budget import BudgetExceeded, remaining_tokens, calls_made_today
from llm.client import ProviderQuotaExhausted
from learning import strategy_learner, level_designer
import git_sync
import incident_log

_ORDER_STATE_PATH = config.STATE_DIR / "cycle_order.json"

_RUNNERS = {
    "strategy_learner": strategy_learner.run_cycle,
    "level_designer": level_designer.run_cycle,
}


def _next_order() -> list[str]:
    """Alternates which learner goes first each cycle. Whichever runs first
    gets first crack at whatever Groq headroom exists that cycle; whichever
    runs second only gets called if the first one didn't already exhaust the
    budget/provider quota. Fixed 2026-08-24: with quota this tight,
    strategy_learner always going first meant level_designer got starved
    almost completely on scarce days (0/20 successful calls one day, 0 new
    knowledge/level_rules_learned.json entries for 2 days straight) — not a
    real prioritization decision, just an accident of hardcoded order."""
    try:
        last_first = json.loads(_ORDER_STATE_PATH.read_text(encoding="utf-8"))["last_first"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        last_first = "strategy_learner"
    order = ["level_designer", "strategy_learner"] if last_first == "strategy_learner" \
        else ["strategy_learner", "level_designer"]
    _ORDER_STATE_PATH.write_text(json.dumps({"last_first": order[0]}), encoding="utf-8")
    return order


def _run_one(name: str) -> bool:
    """Runs one learner, logging its outcome. Returns True if the budget/
    provider quota is exhausted (caller should not attempt the other one)."""
    try:
        result = _RUNNERS[name]()
        print(f"  {name}: {result}", flush=True)
        return False
    except BudgetExceeded as e:
        print(f"  {name}: budget exhausted for today ({e}) — stopping cleanly.", flush=True)
        return True
    except ProviderQuotaExhausted as e:
        print(f"  {name}: provider rate-limited hard ({e}) — stopping cleanly, "
              f"this is Groq/Anthropic's cap, not our bug. See state/rate_limit_events.jsonl.", flush=True)
        return True
    except Exception:  # noqa: BLE001
        print(f"  {name}: unexpected error, see traceback below", flush=True)
        traceback.print_exc()
        incident_log.record_exception(name)
        return False


def main() -> int:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] learning cycle starting "
          f"(budget remaining today: {remaining_tokens()} tokens, {calls_made_today()} calls made)", flush=True)

    first, second = _next_order()
    print(f"  order this cycle: {first} -> {second}", flush=True)

    if not _run_one(first):
        _run_one(second)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] cycle done "
          f"(budget remaining: {remaining_tokens()} tokens)", flush=True)

    try:
        git_sync.sync_cycle_results()
    except Exception:  # noqa: BLE001 — a sync failure shouldn't mask the cycle's own result
        print("  git_sync: failed, see traceback below (this run's results may be lost)", flush=True)
        traceback.print_exc()
        incident_log.record_exception("git_sync")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Entry point for the month-1 learning phase. Runs on a GitHub Actions
schedule (.github/workflows/learning.yml), NOT on your own machine — each
invocation gets a fresh, disposable VM, loops strategy/level-design cycles
back-to-back until the daily token budget or this job's own time budget runs
out, committing+pushing (git_sync.py) after every cycle so the next
invocation — on a completely different disposable VM, possibly hours away —
picks up where this one left off. Also fine to run locally for testing; it
still pushes, same as any other command in this repo would.

Looping instead of running exactly one cycle per invocation is deliberate
(added 2026-08-29): this repo/account is new enough that GitHub's scheduler
only actually grants this workflow ~1-2 real runs/day regardless of the cron
interval requested (see config.JOB_TIME_BUDGET_SECONDS for the full story).
One cycle per run wasted almost the entire day's token budget sitting idle;
looping means each rare run spends as much of that budget as it can.

The daily token budget (llm/budget.py) is tracked in state/budget.json,
which IS committed (see .gitignore) specifically so it survives across
these disposable runners — without that commit, every run would think it
had a fresh 180k-token day and the free-tier cap would mean nothing.

Usage: python run_learning_cycle.py
"""
from __future__ import annotations

import json
import sys
import time
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

# Pure safety net, not the real stopping condition (config.JOB_TIME_BUDGET_SECONDS
# is) — guards against an unforeseen bug making every cycle fail near-instantly
# with zero real API latency, which could otherwise spin and spam commits for
# the whole time budget instead of tripping BudgetExceeded/ProviderQuotaExhausted
# like a normal day does.
_MAX_CYCLE_PAIRS_PER_RUN = 300


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
    start = time.monotonic()
    deadline = start + config.JOB_TIME_BUDGET_SECONDS
    print(f"[{datetime.now().isoformat(timespec='seconds')}] learning cycle starting "
          f"(budget remaining today: {remaining_tokens()} tokens, {calls_made_today()} calls made, "
          f"time budget this run: {config.JOB_TIME_BUDGET_SECONDS}s)", flush=True)

    pairs_run = 0
    while pairs_run < _MAX_CYCLE_PAIRS_PER_RUN:
        pairs_run += 1
        first, second = _next_order()
        print(f"  pair {pairs_run}: order {first} -> {second} "
              f"({time.monotonic() - start:.0f}s elapsed)", flush=True)

        exhausted = _run_one(first)
        if not exhausted:
            exhausted = _run_one(second)

        try:
            git_sync.sync_cycle_results()
        except Exception:  # noqa: BLE001 — a sync failure shouldn't mask the cycle's own result
            print("  git_sync: failed, see traceback below (this cycle's results may be lost)", flush=True)
            traceback.print_exc()
            incident_log.record_exception("git_sync")

        if exhausted:
            print("  stopping loop: today's budget/provider quota is exhausted.", flush=True)
            break
        if time.monotonic() >= deadline:
            print(f"  stopping loop: hit this run's {config.JOB_TIME_BUDGET_SECONDS}s time budget "
                  f"after {pairs_run} pair(s) — next run picks up from here.", flush=True)
            break
    else:
        print(f"  stopping loop: hit the {_MAX_CYCLE_PAIRS_PER_RUN}-pair safety cap — "
              f"investigate if cycles are failing near-instantly.", flush=True)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] run done after {pairs_run} pair(s), "
          f"{time.monotonic() - start:.0f}s (budget remaining: {remaining_tokens()} tokens)", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

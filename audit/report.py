#!/usr/bin/env python
"""Readable rollup of one day's audit trail — the "good way to audit every
detail generated" that a raw JSONL dump isn't. Reads audit/YYYY-MM-DD.jsonl.

Usage:
  python audit/report.py                 # today
  python audit/report.py 2026-08-22       # a specific day
  python audit/report.py --all            # totals across every day recorded
  python audit/report.py 2026-08-22 -v    # include full prompt/response text per call
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# generated text (hypotheses, reasoning) can contain characters the Windows
# console's default codepage can't display (e.g. narrow no-break spaces) —
# replace rather than crash the report over a cosmetic character.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from llm import audit  # noqa: E402


def _print_day(day: str, verbose: bool = False) -> dict:
    entries = audit.read_day(day)
    print(f"\n=== {day} ===")
    if not entries:
        print("  (no calls recorded)")
        return {"day": day, "tokens": 0, "calls": 0}

    tokens = sum(e["tokens_used"] for e in entries)
    by_caller = defaultdict(lambda: {"calls": 0, "tokens": 0})
    for e in entries:
        by_caller[e["caller"]]["calls"] += 1
        by_caller[e["caller"]]["tokens"] += e["tokens_used"]

    print(f"  calls: {len(entries)}   tokens spent: {tokens}   budget remaining after last call: "
          f"{entries[-1]['budget_remaining_after']}")
    print("  by caller:")
    for caller, stats in sorted(by_caller.items()):
        print(f"    {caller:<20} {stats['calls']:>4} calls   {stats['tokens']:>7} tokens")

    print("  achievements this day:")
    for e in entries:
        outcome = e["outcome"]
        tag = e["caller"]
        if outcome.get("promoted"):
            print(f"    [{e['timestamp']}] {tag}: PROMOTED policy "
                  f"{outcome.get('old_win_rate', 0):.0%} -> {outcome.get('new_win_rate', 0):.0%}")
        elif outcome.get("recorded"):
            status = "won" if outcome.get("won") else "lost"
            print(f"    [{e['timestamp']}] {tag}: rule tested ({status}) — {outcome.get('hypothesis', '')[:80]}")
        elif outcome.get("accepted"):
            print(f"    [{e['timestamp']}] {tag}: level accepted ({outcome.get('level_password')})")
        else:
            reason = outcome.get("reason", "no improvement")
            print(f"    [{e['timestamp']}] {tag}: no result ({reason})")

        if verbose:
            print(f"      system: {e['system_prompt'][:200]!r}")
            print(f"      user:   {e['user_prompt'][:200]!r}")
            print(f"      response: {e['response'][:300]!r}")

    return {"day": day, "tokens": tokens, "calls": len(entries)}


def _print_all() -> None:
    days = audit.available_days()
    if not days:
        print("no audit data yet")
        return
    total_tokens = 0
    total_calls = 0
    for day in days:
        stats = _print_day(day)
        total_tokens += stats["tokens"]
        total_calls += stats["calls"]
    print(f"\n=== TOTAL across {len(days)} day(s) ===")
    print(f"  calls: {total_calls}   tokens: {total_tokens}")


def main() -> int:
    args = sys.argv[1:]
    verbose = "-v" in args
    args = [a for a in args if a != "-v"]

    if args and args[0] == "--all":
        _print_all()
        return 0

    day = args[0] if args else date.today().isoformat()
    _print_day(day, verbose=verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Readable rollup of incident_log.py's two trails — answers "is the pipeline
failing because of us or because of Groq/Anthropic" without grepping JSONL by
hand. Companion to audit/report.py (which covers successful calls only).

Usage:
  python incident_report.py            # everything recorded so far
  python incident_report.py -v         # include full raw detail per event
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import incident_log  # noqa: E402
from llm import rate_limits  # noqa: E402


def main() -> int:
    verbose = "-v" in sys.argv[1:]

    quota_events = rate_limits.read_all()
    errors = incident_log.read_errors()

    print("=== Provider rate-limit events (Groq/Anthropic's fault) ===")
    if not quota_events:
        print("  (none recorded)")
    else:
        gave_up = [e for e in quota_events if e["gave_up"]]
        print(f"  {len(quota_events)} total 429s, {len(gave_up)} we gave up on "
              f"(retry-after exceeded threshold)")
        for e in quota_events:
            tag = "GAVE UP" if e["gave_up"] else "retried"
            print(f"  [{e['timestamp']}] {e['provider']} attempt {e['attempt']} "
                  f"wait={e['wait_seconds']}s -> {tag}")
            if verbose:
                print(f"      {e['detail']}")

    print("\n=== Our own code errors (our bug) ===")
    if not errors:
        print("  (none recorded)")
    else:
        print(f"  {len(errors)} total")
        for e in errors:
            first_line = e["detail"].strip().splitlines()[-1] if e["detail"].strip() else ""
            print(f"  [{e['timestamp']}] {e['source']}: {first_line}")
            if verbose:
                print(f"      {e['detail']}")

    if not quota_events and not errors:
        print("\nnothing recorded — either everything has run clean, or these logs "
              "were only added 2026-08-23 and predate what you're looking for.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

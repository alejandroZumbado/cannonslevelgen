"""Durable trail of every 429 this pipeline hits from a provider (Groq or
Anthropic) — the "is this Groq's fault" half of incident_log.py's split.

Written by llm/client.py on every 429, whether we retried it or gave up on
it. This exists because of the 2026-08-23 incident: ~20 straight CI runs of
learning.yml hung for their full 10-minute timeout-minutes and got killed,
with zero visible output (stdout buffers without a TTY, so even the first
print() never flushed before the kill). The likely cause — confirmed by
correlation, not by catching the 429 live — was a Groq daily/hourly quota
exhausting server-side sometime after 09:39 UTC, each retry then sleeping on
a `retry-after` far longer than the job had left to live. This file is what
lets a future incident be diagnosed from the committed state instead of
guessed at after the fact: it's committed via git_sync.py like state/budget.json
so it survives the disposable runners.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config

_PATH = config.STATE_DIR / "rate_limit_events.jsonl"


def record(*, provider: str, attempt: int, wait_seconds: float, gave_up: bool, detail: str,
           tpd: dict | None = None) -> None:
    """`tpd`, when known (parsed from a Groq daily-quota 429 body by
    llm.client._parse_tpd — Groq doesn't expose this on headers, only in the
    429 text), is {"limit": int, "used": int, "requested": int}. Recording it
    turns this log into a real trend of how close to the daily cap the
    pipeline is running, not just a yes/no of whether a 429 happened."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider,
        "attempt": attempt,
        "wait_seconds": round(wait_seconds, 1),
        "gave_up": gave_up,
        "detail": detail[:300],
    }
    if tpd is not None:
        entry["tpd"] = tpd
    with open(_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_all() -> list[dict]:
    if not _PATH.exists():
        return []
    return [json.loads(line) for line in _PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

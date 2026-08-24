"""Persisted per-provider cooldown, keyed off the real retry-after Groq/
Anthropic gave us in llm/client.py's ProviderQuotaExhausted path.

Why this exists on top of the fail-fast fix (llm/rate_limits.py logs the
429, client.py stops sleeping through it): without this, a provider quota
that's exhausted for e.g. 8 minutes would still get one real, doomed HTTP
request from EVERY scheduled run in that window — learning.yml's cron fires
every 15 min, so that's still 1-2 wasted, guaranteed-429 requests per
incident, and each is a fresh disposable CI runner that has no memory of the
last one's 429 unless it's committed here. This makes the next call attempt
check disk FIRST and skip the request entirely if still inside a persisted
cooldown, so "we're out of tokens" stops turning into "keep asking anyway."
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config

_PATH = config.STATE_DIR / "cooldown.json"


def _load() -> dict:
    if not _PATH.exists():
        return {}
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_cooldown(provider: str, resume_at: datetime, detail: str) -> None:
    data = _load()
    data[provider] = {"resume_at": resume_at.isoformat(timespec="seconds"), "detail": detail[:300]}
    _save(data)


def clear(provider: str) -> None:
    data = _load()
    if provider in data:
        del data[provider]
        _save(data)


def resume_at(provider: str) -> datetime | None:
    """Returns the persisted resume time for `provider` if it's still in the
    future. If a cooldown entry exists but has already elapsed (e.g. a run
    starting right as the window closes), clears it and returns None — the
    entry is stale, the next call should go through normally."""
    data = _load()
    entry = data.get(provider)
    if not entry:
        return None
    when = datetime.fromisoformat(entry["resume_at"])
    if datetime.now(timezone.utc) >= when:
        clear(provider)
        return None
    return when

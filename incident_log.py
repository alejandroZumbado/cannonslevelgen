"""Durable, git-synced trail of things that go wrong, split by WHOSE fault it
is — separate from llm/audit.py (which only records completed calls). Lets a
human tell, without re-reading raw CI logs (which expire and were also silent
during the 2026-08-23 incident due to stdout buffering), whether a bad
stretch was a provider (Groq/Anthropic) rate-limiting us hard
(rate_limit_events.jsonl, written by llm/client.py) or a bug in this
project's own code (error_events.jsonl, written by callers like
run_learning_cycle.py). Both live under state/, which git_sync.py already
commits every cycle — see the .gitignore note on why state/ is tracked.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone

import config

ERROR_LOG_PATH = config.STATE_DIR / "error_events.jsonl"


def _append(path, entry: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record_error(*, source: str, detail: str) -> None:
    """Use for a caught exception that is OUR bug (code, not provider limits)."""
    _append(ERROR_LOG_PATH, {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "detail": detail[:2000],
    })


def record_exception(source: str) -> None:
    """Convenience: call from inside an `except:` block to log the current
    exception's traceback under `source`."""
    record_error(source=source, detail=traceback.format_exc())


def read_errors() -> list[dict]:
    if not ERROR_LOG_PATH.exists():
        return []
    return [json.loads(line) for line in ERROR_LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

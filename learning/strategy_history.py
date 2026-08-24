"""Rolling history of strategy_learner's own promote/reject outcomes.

Two things this unlocks that plain audit/report.py couldn't, because that's
organized by CALENDAR DAY (one file per day) while a losing streak spans
days:
1. Each new cycle's prompt can see a short summary of what it already tried
   and rejected recently, so the LLM stops re-proposing near-identical ideas
   cycle after cycle.
2. `consecutive_rejections()` lets the caller detect a real losing streak
   (not just "today had no promotion") and switch strategy — e.g. from
   "propose a full rewrite" to "propose one small targeted fix" — instead of
   letting a full-rewrite framing bounce between different-but-equally-
   mediocre designs indefinitely. Added 2026-08-24 after strategy_learner
   went 10 straight cycles without a promotion (only one ever, 76%->79% on
   2026-08-23) following the exact "propose an improved full replacement"
   framing every single time.

Separate from learning/knowledge.py's LearnedRule list, which is level-design
hypotheses, not policy-authoring attempts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config

_PATH = config.KNOWLEDGE_DIR / "strategy_history.json"
_MAX_ENTRIES = 20  # trailing window only — old attempts stop being useful context


def _load() -> list[dict]:
    if not _PATH.exists():
        return []
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _save(entries: list[dict]) -> None:
    _PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def record(*, promoted: bool, win_rate: float, candidate_win_rate: float, summary: str) -> None:
    entries = _load()
    entries.append({
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "promoted": promoted,
        "win_rate": round(win_rate, 4),
        "candidate_win_rate": round(candidate_win_rate, 4),
        "summary": summary[:220],
    })
    _save(entries[-_MAX_ENTRIES:])


def recent(n: int = 4) -> list[dict]:
    return _load()[-n:]


def consecutive_rejections() -> int:
    """Cycles in a row (most recent first) that failed to promote — resets
    to 0 the moment a promotion happens."""
    count = 0
    for entry in reversed(_load()):
        if entry["promoted"]:
            break
        count += 1
    return count


def recent_attempts_block(n: int = 4) -> str:
    """Renders recent rejected attempts for injection into the prompt, so
    the LLM sees what NOT to repeat. Empty string if there's no history yet
    (first-ever cycle, or history predates this feature)."""
    entries = [e for e in recent(n) if not e["promoted"]]
    if not entries:
        return ""
    lines = [
        f"- ({e['candidate_win_rate']:.0%} vs {e['win_rate']:.0%} champion) {e['summary']}"
        for e in entries
    ]
    return "\n".join(lines)

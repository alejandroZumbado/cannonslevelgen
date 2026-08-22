"""Persisted daily token budget — makes the month-long learning loop safe to run
unattended: it can only ever spend up to DAILY_TOKEN_BUDGET per calendar day,
across as many separate process invocations (Task Scheduler runs) as happen
that day, because the counter lives on disk, not in memory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import config


class BudgetExceeded(Exception):
    pass


@dataclass
class _State:
    day: str
    tokens_used: int
    calls_made: int


def _path():
    return config.STATE_DIR / "budget.json"


def _today() -> str:
    return date.today().isoformat()


def _load() -> _State:
    p = _path()
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("day") == _today():
            return _State(**d)
    return _State(day=_today(), tokens_used=0, calls_made=0)


def _save(state: _State) -> None:
    _path().write_text(json.dumps(state.__dict__, indent=2), encoding="utf-8")


def remaining_tokens() -> int:
    state = _load()
    return max(0, config.DAILY_TOKEN_BUDGET - state.tokens_used)


def calls_made_today() -> int:
    return _load().calls_made


def record_usage(tokens: int) -> None:
    state = _load()
    state.tokens_used += tokens
    state.calls_made += 1
    _save(state)


def check_can_spend(estimated_tokens: int) -> None:
    if remaining_tokens() < estimated_tokens:
        raise BudgetExceeded(
            f"daily token budget exhausted: {remaining_tokens()} left, "
            f"need ~{estimated_tokens}. Resumes automatically tomorrow."
        )
    if calls_made_today() >= config.GROQ_RPD_LIMIT:
        raise BudgetExceeded("Groq daily request cap reached, resumes tomorrow.")

"""Per-call audit trail — one JSON line per LLM call, every single day, with
the full prompt, full response, exact token cost, and what it actually
produced (promoted a policy? confirmed a rule? nothing?).

This is the raw paper trail. `learning_log/*.md` stays the short human
narrative; `budget.json` stays the cheap aggregate counter the budget gate
checks on every call; THIS is what you read when you want to audit a
specific day down to the exact prompt that was sent. See audit/report.py for
a readable rollup instead of grepping JSONL by hand.
"""
from __future__ import annotations

import json
from datetime import datetime

import config


def _path(day: str | None = None):
    day = day or datetime.now().date().isoformat()
    return config.AUDIT_DIR / f"{day}.jsonl"


def record_call(*, caller: str, completion, system: str, user: str, outcome: dict) -> None:
    """`completion` is an llm.client.Completion (text, tokens_used, provider, model).
    `outcome` is a small caller-defined dict describing what the call actually
    achieved — e.g. {"promoted": True, "win_rate_before": .68, "win_rate_after": .74}
    or {"rule_confirmed": True, "won": True}. Always JSON-serializable, plain values only."""
    import llm.budget as budget  # local import: avoids a budget<->audit import cycle

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "caller": caller,
        "provider": completion.provider,
        "model": completion.model,
        "tokens_used": completion.tokens_used,
        "budget_remaining_after": budget.remaining_tokens(),
        "system_prompt": system,
        "user_prompt": user,
        "response": completion.text,
        "outcome": outcome,
    }
    with open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_day(day: str | None = None) -> list[dict]:
    path = _path(day)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def available_days() -> list[str]:
    return sorted(p.stem for p in config.AUDIT_DIR.glob("*.jsonl"))

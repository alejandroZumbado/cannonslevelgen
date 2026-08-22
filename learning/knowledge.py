"""Persistent knowledge the AI accumulates on its own — separate from (and
meant to eventually supersede) the human-authored rules in Cannons/CLAUDE.md
and the project's own memory notes. Those are seed hypotheses, not ceilings.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date

import config

RULES_PATH = config.KNOWLEDGE_DIR / "level_rules_learned.json"


@dataclass
class LearnedRule:
    date: str
    statement: str          # e.g. "HP3 pirate alone in a column, spawned round>=3, is winnable without a merge"
    evidence: str            # short description of how it was tested (levels/policy/win-rate)
    confidence: float        # 0-1, self-reported by the model, informational only
    source: str               # "level_designer" | "strategy_learner"


def _load_rules() -> list[dict]:
    if RULES_PATH.exists():
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return []


def add_rule(rule: LearnedRule) -> None:
    rules = _load_rules()
    rules.append(asdict(rule))
    RULES_PATH.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")


def all_rules() -> list[dict]:
    return _load_rules()


def rules_as_prompt_block() -> str:
    """Renders learned rules for injection into future LLM prompts, so each
    day's generation builds on what was confirmed before instead of
    rediscovering it."""
    rules = _load_rules()
    if not rules:
        return "(no confirmed rules yet)"
    return "\n".join(f"- {r['statement']} (confidence {r['confidence']:.2f}, {r['date']})" for r in rules)


def _log_path(day: str | None = None) -> "config.Path":
    day = day or date.today().isoformat()
    return config.LEARNING_LOG_DIR / f"{day}.md"


def append_log(section_title: str, body: str) -> None:
    """Appends a dated, human-readable entry — this is what you'd actually read
    to check on a month-long unattended run without digging through JSON."""
    path = _log_path()
    header = f"# {date.today().isoformat()}\n\n" if not path.exists() else ""
    entry = f"{header}## {section_title}\n\n{body}\n\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)

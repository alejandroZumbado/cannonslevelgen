"""Snapshot of the REAL game's winnable levels, for the strategy learner.

Why (measured 2026-09-22): the synthetic benchmark is saturated — the
champion already wins 97.9% of its WINNABLE levels; the ~11% it "loses" are
mostly impossible random draws. A candidate policy can only gain ~2 levels
per suite, which is noise, so strategy_learner went 11 days / ~130
proposals without a promotion. The real game has 41 levels the champion
loses but the wide search solver wins (`solved_by_search_only`) — genuine,
learnable headroom — plus ~300 it wins, which guard against regressions.

The learning workflow has no Cannons checkout, so the weekly audit (which
does) writes the layouts here: reports/real_suite.json. Missing file = the
learner just runs on the synthetic suite as before.
"""
from __future__ import annotations

import json

import config
from sim.level import Level

SUITE_PATH = config.ROOT / "reports" / "real_suite.json"
TARGET = "solved_by_search_only"  # champion loses, solver wins
GUARD = "champion_win"


def write_suite(levels: list[Level], audit_report: dict) -> int:
    """Stores every winnable real level with its audit classification.
    Returns how many were written."""
    classification = {r["levelNumber"]: r["classification"] for r in audit_report["levels"]}
    rows = [
        {"classification": classification[lvl.levelNumber], "level": lvl.to_dict()}
        for lvl in levels
        if classification.get(lvl.levelNumber) in (TARGET, GUARD)
    ]
    SUITE_PATH.write_text(json.dumps({
        "generated_from_audit": audit_report["generated_at"],
        "levels": rows,
    }, ensure_ascii=False), encoding="utf-8")
    return len(rows)


def load_suite() -> tuple[list[Level], list[Level]]:
    """(targets, guards). Both empty if the snapshot doesn't exist yet."""
    if not SUITE_PATH.exists():
        return [], []
    data = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    targets = [Level.from_dict(r["level"]) for r in data["levels"] if r["classification"] == TARGET]
    guards = [Level.from_dict(r["level"]) for r in data["levels"] if r["classification"] == GUARD]
    return targets, guards


def describe_level(level: Level) -> str:
    """Compact one-line-per-round layout for prompts: `R3: c1 hp2, c4 hp1`.
    Column 0 = right .. 4 = left, same as the engine."""
    lines = []
    for i, fila in enumerate(level.filas, start=1):
        pirates = [f"c{c.index} hp{c.hp}" for c in fila.cuadros if c.tipo >= 1]
        lines.append(f"R{i}: " + (", ".join(pirates) if pirates else "-"))
    return "\n".join(lines)

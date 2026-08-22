"""Scores a policy against a suite of levels — the single number the learning
loop uses to decide whether a proposed policy is actually an improvement."""
from __future__ import annotations

from dataclasses import dataclass

from sim.engine import run_level
from sim.level import Level


@dataclass
class Score:
    wins: int
    total: int
    win_rate: float
    avg_rounds_played: float

    def better_than(self, other: "Score") -> bool:
        if self.win_rate != other.win_rate:
            return self.win_rate > other.win_rate
        # tie-break: fewer rounds played on average = more decisive play
        return self.avg_rounds_played < other.avg_rounds_played


def evaluate(policy, levels: list[Level], max_rounds: int = 200) -> Score:
    wins = 0
    rounds_total = 0
    for level in levels:
        engine = run_level(level, policy, max_rounds=max_rounds)
        if engine.won:
            wins += 1
        rounds_total += engine.rounds_played
    total = len(levels)
    return Score(
        wins=wins,
        total=total,
        win_rate=wins / total if total else 0.0,
        avg_rounds_played=rounds_total / total if total else 0.0,
    )

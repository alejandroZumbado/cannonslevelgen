"""v0 baseline policy — deliberately naive. This is the starting point the
learning loop (learning/strategy_learner.py) is meant to replace over the
month-1 learning phase, NOT a hand-tuned "good" strategy. Do not "improve"
this file by hand — improvements are supposed to come from the AI's own
self-play, that's the point of the project.
"""
from __future__ import annotations

NUM_COLUMNS = 5


class BaselinePolicy:
    name = "baseline_v0"

    def choose_action(self, engine):
        threats = self._threat_by_column(engine)

        empty_threatened = [c for c in range(NUM_COLUMNS) if c not in engine.cannons and threats[c] >= 0]
        if empty_threatened:
            col = max(empty_threatened, key=lambda c: threats[c])
            return ("spawn", col)

        undersized = [
            c for c, cannon in engine.cannons.items()
            if threats[c] >= 0 and cannon.damage < self._needed_damage(engine, c)
        ]
        if undersized:
            col = max(undersized, key=lambda c: threats[c])
            return ("spawn", col)

        empty_cols = [c for c in range(NUM_COLUMNS) if c not in engine.cannons]
        if empty_cols:
            return ("spawn", empty_cols[0])

        weakest = min(engine.cannons.items(), key=lambda kv: kv[1].damage)[0]
        return ("spawn", weakest)

    @staticmethod
    def _threat_by_column(engine) -> dict[int, int]:
        threats = {c: -1 for c in range(NUM_COLUMNS)}
        for p in engine.pirates:
            if p.position > threats[p.column]:
                threats[p.column] = p.position
        return threats

    @staticmethod
    def _needed_damage(engine, column: int) -> int:
        pirates_here = [p for p in engine.pirates if p.column == column]
        return max((p.hp for p in pirates_here), default=1)

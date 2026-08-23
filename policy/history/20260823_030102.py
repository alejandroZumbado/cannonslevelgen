"""Active policy. Starts as a copy of baseline_v0 — learning/strategy_learner.py
rewrites this file's content whenever it finds code that beats this file's own
benchmark score. Every previous version is archived under policy/history/
before being overwritten, so nothing is ever lost.

Must define: class Policy with method choose_action(self, engine) -> action,
where action is ("spawn", col) or ("move", from_col, to_col). No imports beyond
the standard library — this file gets exec'd in a restricted sandbox.
"""

NUM_COLUMNS = 5


class Policy:
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
    def _threat_by_column(engine):
        threats = {c: -1 for c in range(NUM_COLUMNS)}
        for p in engine.pirates:
            if p.position > threats[p.column]:
                threats[p.column] = p.position
        return threats

    @staticmethod
    def _needed_damage(engine, column):
        pirates_here = [p for p in engine.pirates if p.column == column]
        return max((p.hp for p in pirates_here), default=1)

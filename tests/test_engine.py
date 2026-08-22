"""Sanity tests for sim/engine.py against known-good hand-derived cases.
Run with: python -m pytest tests/ -q   (or: python tests/test_engine.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.level import Level, Fila, Cuadro
from sim.engine import GameEngine, run_level
from policy.baseline import BaselinePolicy


def lvl(filas_spec, level_number=1):
    filas = [Fila(cuadros=[Cuadro(index=i, tipo=t, hp=h) for (i, t, h) in fila]) for fila in filas_spec]
    return Level(levelNumber=level_number, password="X0000", isHard=False, filas=filas)


def test_single_weak_pirate_is_winnable():
    level = lvl([[(2, 1, 1)]])
    engine = run_level(level, BaselinePolicy())
    assert engine.won, "single HP1 pirate at a column the baseline always defends should be winnable"


def test_undefended_column_loses():
    # HP high enough, and never place a cannon there: force a loss by using a
    # do-nothing-useful policy that always dumps cannons on column 0 while the
    # pirate is in column 4, several rows so it has time to walk off the board.
    level = lvl([[(4, 1, 1)], [], [], []])

    class IgnoreColumn4(BaselinePolicy):
        def choose_action(self, engine):
            return ("spawn", 0)

    engine = run_level(level, IgnoreColumn4())
    assert not engine.won
    assert engine.game_ended


def test_hp3_beatable_with_damage_1_no_merge():
    # a single HP3 pirate, alone in its column, with several rows of buildup
    # before it spawns is exactly the documented boundary case (HP<=3 beatable
    # with base damage 1, no merge required) — see CLAUDE.md "Level design constraints".
    level = lvl([[], [(2, 1, 3)]])
    engine = run_level(level, BaselinePolicy())
    assert engine.won


def test_hp4_unbeatable_without_merge_against_naive_policy():
    # HP4 needs a merge (damage 2+) per the documented timing rule. Placing the
    # spawn cannon at column 2 once, then parking every later spawn on column 0
    # (never touching column 2 again), keeps column 2 stuck at damage 1 forever
    # — genuinely "never merge", unlike repeatedly spawning on the same column
    # (which merges every time and is a different test below).
    level = lvl([[(2, 1, 4)]])

    class NeverMerge(BaselinePolicy):
        def choose_action(self, engine):
            return ("spawn", 2) if 2 not in engine.cannons else ("spawn", 0)

    engine = run_level(level, NeverMerge())
    assert not engine.won


def test_merge_produces_damage_2_and_can_beat_hp4():
    # place the first spawn cannon at column 2, then merge the second spawn
    # cannon onto column 2 (damage 1+1=2) before the HP4 pirate is in range.
    level = lvl([[], [(2, 1, 4)]])

    class MergeThenDefend(BaselinePolicy):
        def choose_action(self, engine):
            if 2 not in engine.cannons:
                return ("spawn", 2)
            if engine.cannons[2].damage == 1:
                return ("spawn", 2)  # merges onto column 2 -> damage 2
            return ("spawn", 0)  # park subsequent spawns elsewhere

    engine = run_level(level, MergeThenDefend())
    assert engine.won


def test_win_condition_matches_no_waves_left_and_no_pirates():
    level = lvl([[(0, 1, 1)]])
    engine = run_level(level, BaselinePolicy())
    assert engine.won
    assert len(engine.pirates) == 0
    assert engine.round >= len(level.filas)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)

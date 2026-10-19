"""Drag check (2026-10-14) — does winning a level REQUIRE dragging a placed cannon?

Why: the user wants the drag mechanic (moving an already placed cannon to
another column, usually a merged one, for a late kill) to be something levels
actually demand. The naive baseline never moves a cannon, but it also plays
greedily, so "naive loses" can mean "needs drag" OR just "needs better spawn
choices". This answers the narrow question directly: the same beam-search
solver as verification/solver.py, restricted to spawn actions only. If THAT
can't win but the full solver (or the champion) can, the level needs a drag.

Example that needs it (user's level, round 1 first, column 0..4 left to right):
  R1: c2 hp1, c3 hp2 / R2: c2 hp2, c3 hp2 / R3: c2 hp2, c3 hp1 / R4: c1 hp8, c3 hp1
Winning line: build damage in column 2 while chipping the HP-8 with a fresh
cannon in column 1, then drag column 2's cannon onto column 1 (merge) for the
kill. Spawn-only play can't reach 8 damage in its 3 shots.
"""
from __future__ import annotations

from sim.engine import NUM_COLUMNS, Action, GameEngine
from sim.level import Level
from verification import solver


class NoDragEngine(GameEngine):
    """Same rules, but the player may only drop the NEW cannon (no moves)."""

    def valid_actions(self) -> list[Action]:
        return [("spawn", c) for c in range(NUM_COLUMNS)]

    def clone(self) -> "NoDragEngine":
        # deepcopy keeps the subclass, stated explicitly so a future override
        # of GameEngine.clone can't silently turn drag back on mid-search
        clone = super().clone()
        clone.__class__ = NoDragEngine
        return clone


def winnable_without_drag(level: Level, widths: tuple[int, ...] = solver.ESCALATION_BEAM_WIDTHS) -> bool:
    """True if spawn-only play can win. Search runs the normal solver with its
    engine class swapped for NoDragEngine, restored even if the search raises."""
    original = solver.GameEngine
    solver.GameEngine = NoDragEngine
    try:
        return solver.solve_thoroughly(level, widths=widths).won
    finally:
        solver.GameEngine = original


def needs_drag(level: Level, widths: tuple[int, ...] = solver.ESCALATION_BEAM_WIDTHS) -> bool:
    """True if spawn-only play can't win. Says nothing about whether the level
    is winnable at all — callers check that separately (champion / solver)."""
    return not winnable_without_drag(level, widths)

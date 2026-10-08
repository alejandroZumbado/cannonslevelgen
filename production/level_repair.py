"""Local repair of an LLM level candidate (2026-10-08) — keeps the LLM's idea,
fixes its numbers, no extra LLM call.

Why: on 10-07 and 10-08 daily_generator spent 16 calls (~44k tokens) and
shipped nothing — 14 candidates were unwinnable (the LLM stacks HP-3 walls
the trained AI can't clear), 2 broke pacing. Every one was thrown away. The
same candidates replayed offline: 15/16 became good levels in ~1 s each with
two hill climbs over the regulator's small edits (verification/level_mutations):

  1. soften — shave / remove / split / drop a round until the champion
     policy wins, climbing on "rounds survived, then less total HP" (a loss
     gives no other gradient);
  2. harden — verification/skill_pass.search with a "harden" goal: the
     naive player (never moves a cannon) must LOSE, the champion still wins,
     pacing ok, size kept, a pirate gets past the first row.

Returns None when either step fails; the caller treats that like any other
rejected candidate.
"""
from __future__ import annotations

import random

from policy.baseline import BaselinePolicy
from sim.engine import run_level
from sim.level import Level
from verification import skill_pass
from verification.level_mutations import drop_fila, normalize, remove, shave, split

# softening edits and their weights: shaving HP keeps the shape best
_SOFTEN_OPS = {"shave": (shave, 4), "remove": (remove, 2), "split": (split, 2), "drop_fila": (drop_fila, 1)}
SOFTEN_MAX_EVALS = 400


def _closeness(level: Level, policy) -> tuple:
    """Higher = closer to a champion win: won, then rounds survived, then
    less total HP left to chew through."""
    engine = run_level(level, policy)
    total_hp = sum(c.hp for f in level.filas for c in f.cuadros if c.tipo >= 1)
    return engine.won, engine.rounds_played, -total_hp


def soften(level: Level, policy, rng: random.Random) -> Level | None:
    """Smallest-step edits until `policy` wins; None if it never does.
    Sideways moves (equal closeness) are accepted to cross plateaus."""
    best = normalize(level)
    best_key = _closeness(best, policy)
    names = list(_SOFTEN_OPS)
    weights = [w for _, w in _SOFTEN_OPS.values()]
    for _ in range(SOFTEN_MAX_EVALS):
        if best_key[0]:
            return best
        cand = _SOFTEN_OPS[rng.choices(names, weights=weights)[0]][0](best, rng)
        if cand is None:
            continue
        key = _closeness(cand, policy)
        if key >= best_key:
            best, best_key = cand, key
    return best if best_key[0] else None


def repair(level: Level, archetype: str | None, taken: set[str], seed: int = 0) -> Level | None:
    """Winnable by the champion, lost by the naive player, pacing ok — or None.
    Tries to land on `archetype` (the day's variety target) first, then
    accepts any archetype rather than losing the level. `taken` = shape
    signatures already in the game (a repaired level may not copy one)."""
    evaluate = skill_pass.Evaluator()
    rng = random.Random(seed)
    soft = soften(level, evaluate.champion, rng)
    if soft is None:
        return None
    for target in ([archetype, None] if archetype else [None]):
        goal = skill_pass.Goal("harden", target_archetype=target)
        best, _, after, left, _ = skill_pass.search(soft, goal, evaluate, taken, rng)
        # search's penalty has no duplicate check for non-breathers: do it here
        if left == 0 and best.shape_signature() not in taken:
            # belt and braces: the two facts the daily gate cares about most
            if after.champion_won and not run_level(best, BaselinePolicy()).won:
                best.levelNumber, best.password, best.isHard = level.levelNumber, level.password, level.isHard
                return best
    return None

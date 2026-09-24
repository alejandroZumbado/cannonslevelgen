"""Small, local edits to a Level — the moves the regulator (verification/
regulator.py) searches over. Each operator returns a NEW level (the input is
never mutated) or None when it doesn't apply (e.g. "split" on a level with no
pirate tall enough). None of them judges quality: every result still has to be
simulated (winnable?) and pacing-checked by the caller.

Invariants every operator keeps (the real game's, see sim/level.py):
  - hp 1-10, one pirate per column per fila, every fila has >= 1 pirate;
  - exactly one tipo-4 "last pirate", on the final fila (re-applied by
    `normalize` after every edit, since edits can move/delete it).
"""
from __future__ import annotations

import copy
import random

from sim.level import Cuadro, Fila, Level

NUM_COLUMNS = 5
MAX_HP = 10


def _pirates(fila: Fila) -> list[Cuadro]:
    return [c for c in fila.cuadros if c.tipo >= 1]


def _free_columns(fila: Fila) -> list[int]:
    used = {c.index for c in _pirates(fila)}
    return [c for c in range(NUM_COLUMNS) if c not in used]


def _new_pirate(rng: random.Random, column: int, hp: int) -> Cuadro:
    return Cuadro(index=column, tipo=rng.choice([1, 2, 3]), hp=max(1, min(MAX_HP, hp)))


def normalize(level: Level) -> Level:
    """Copy that satisfies the invariants above: drops tipo-0 cuadros, clamps
    hp, removes empty filas, and puts the single tipo-4 on the final fila's
    tallest pirate (cosmetic skin only, but the game expects exactly one)."""
    lv = copy.deepcopy(level)
    filas = []
    for fila in lv.filas:
        cuadros = []
        for c in _pirates(fila):
            if any(k.index == c.index for k in cuadros):
                continue  # duplicate column in one fila: keep the first
            cuadros.append(Cuadro(index=c.index, tipo=1 if c.tipo == 4 else c.tipo,
                                  hp=max(1, min(MAX_HP, c.hp))))
        if cuadros:
            filas.append(Fila(cuadros=cuadros))
    lv.filas = filas
    if filas:
        max(filas[-1].cuadros, key=lambda c: c.hp).tipo = 4
    return lv


def _pick_pirate(level: Level, rng: random.Random, fila_from: float = 0.0,
                 cond=lambda c: True) -> tuple[int, int] | None:
    """(fila index, cuadro index) of a random pirate satisfying `cond`, only
    among filas at or after `fila_from` (0..1 of the level's length)."""
    start = int(len(level.filas) * fila_from)
    options = [(fi, ci) for fi in range(start, len(level.filas))
               for ci, c in enumerate(level.filas[fi].cuadros) if c.tipo >= 1 and cond(c)]
    return rng.choice(options) if options else None


# ---- operators ------------------------------------------------------------

def shave(level: Level, rng: random.Random) -> Level | None:
    """-1 HP on one pirate (makes a broken level more winnable)."""
    pick = _pick_pirate(level, rng, cond=lambda c: c.hp >= 2)
    if pick is None:
        return None
    lv = copy.deepcopy(level)
    lv.filas[pick[0]].cuadros[pick[1]].hp -= 1
    return normalize(lv)


def bump_late(level: Level, rng: random.Random) -> Level | None:
    """+1 HP on a pirate in the second half (more late pressure)."""
    pick = _pick_pirate(level, rng, fila_from=0.5, cond=lambda c: c.hp < MAX_HP)
    if pick is None:
        return None
    lv = copy.deepcopy(level)
    lv.filas[pick[0]].cuadros[pick[1]].hp += 1
    return normalize(lv)


def remove(level: Level, rng: random.Random) -> Level | None:
    """Deletes one pirate from a fila that has >= 2 (never empties a fila)."""
    options = [fi for fi, f in enumerate(level.filas) if len(_pirates(f)) >= 2]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fila = lv.filas[rng.choice(options)]
    fila.cuadros.remove(rng.choice(_pirates(fila)))
    return normalize(lv)


def add_late(level: Level, rng: random.Random) -> Level | None:
    """Adds a pirate (HP 1-3) to a second-half fila with a free column —
    width over height: more pirates, not taller ones."""
    start = len(level.filas) // 2
    options = [fi for fi in range(start, len(level.filas)) if _free_columns(level.filas[fi])]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fila = lv.filas[rng.choice(options)]
    fila.cuadros.append(_new_pirate(rng, rng.choice(_free_columns(fila)), rng.randint(1, 3)))
    return normalize(lv)


def widen_single(level: Level, rng: random.Random) -> Level | None:
    """Adds a pirate (HP 1-2) to a fila that has exactly one."""
    options = [fi for fi, f in enumerate(level.filas) if len(_pirates(f)) == 1]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fila = lv.filas[rng.choice(options)]
    fila.cuadros.append(_new_pirate(rng, rng.choice(_free_columns(fila)), rng.randint(1, 2)))
    return normalize(lv)


def split(level: Level, rng: random.Random) -> Level | None:
    """One pirate of HP h (>= 4) becomes two in the same fila, HP summing to h
    (2 x HP7 instead of 1 x HP14 — the user's rule)."""
    pick = _pick_pirate(level, rng, cond=lambda c: c.hp >= 4)
    if pick is None or not _free_columns(level.filas[pick[0]]):
        return None
    lv = copy.deepcopy(level)
    fila = lv.filas[pick[0]]
    tall = fila.cuadros[pick[1]]
    first = rng.randint(2, tall.hp - 2)
    column = rng.choice(_free_columns(fila))
    fila.cuadros.append(_new_pirate(rng, column, tall.hp - first))
    tall.hp = first
    return normalize(lv)


def move_column(level: Level, rng: random.Random) -> Level | None:
    """Moves one pirate to a free column of its own fila (unstacks walls,
    creates side switches)."""
    options = [(fi, c) for fi, f in enumerate(level.filas) if _free_columns(f) for c in _pirates(f)]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fi, original = rng.choice(options)
    fila = lv.filas[fi]
    target = next(c for c in fila.cuadros if c.index == original.index)
    target.index = rng.choice(_free_columns(fila))
    return normalize(lv)


def shift_later(level: Level, rng: random.Random) -> Level | None:
    """Moves one pirate to the next fila (same column, if free) — spreads a
    crowded early round, pushes danger toward the end."""
    options = [(fi, c) for fi in range(len(level.filas) - 1) if len(_pirates(level.filas[fi])) >= 2
               for c in _pirates(level.filas[fi])
               if c.index in _free_columns(level.filas[fi + 1])]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fi, original = rng.choice(options)
    moved = next(c for c in lv.filas[fi].cuadros if c.index == original.index)
    lv.filas[fi].cuadros.remove(moved)
    lv.filas[fi + 1].cuadros.append(moved)
    return normalize(lv)


def merge_filas(level: Level, rng: random.Random) -> Level | None:
    """Joins two consecutive filas whose columns don't overlap into one —
    shortens long, sparse levels without deleting any pirate."""
    options = [fi for fi in range(len(level.filas) - 1)
               if not ({c.index for c in _pirates(level.filas[fi])}
                       & {c.index for c in _pirates(level.filas[fi + 1])})]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fi = rng.choice(options)
    lv.filas[fi].cuadros.extend(lv.filas[fi + 1].cuadros)
    del lv.filas[fi + 1]
    return normalize(lv)


def drop_fila(level: Level, rng: random.Random) -> Level | None:
    """Deletes a whole single-pirate fila (last resort for too-long levels)."""
    options = [fi for fi, f in enumerate(level.filas[:-1]) if len(_pirates(f)) == 1]
    if not options or len(level.filas) <= 5:
        return None
    lv = copy.deepcopy(level)
    del lv.filas[rng.choice(options)]
    return normalize(lv)


def stack(level: Level, rng: random.Random) -> Level | None:
    """Adds a pirate (HP 1-3) right behind an existing one: same column, next
    fila — the front pirate blocks shots at it ("wall" archetype)."""
    options = [(fi, c.index) for fi in range(len(level.filas) - 1) for c in _pirates(level.filas[fi])
               if c.index in _free_columns(level.filas[fi + 1])]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fi, column = rng.choice(options)
    lv.filas[fi + 1].cuadros.append(_new_pirate(rng, column, rng.randint(1, 3)))
    return normalize(lv)


def grow(level: Level, rng: random.Random) -> Level | None:
    """+2 HP on one pirate that already has >= 3 ("tank" archetype: few tall
    pirates that need merges prepared in their column)."""
    pick = _pick_pirate(level, rng, cond=lambda c: 3 <= c.hp <= MAX_HP - 2)
    if pick is None:
        return None
    lv = copy.deepcopy(level)
    lv.filas[pick[0]].cuadros[pick[1]].hp += 2
    return normalize(lv)


OPERATORS = {
    "shave": shave, "bump_late": bump_late, "remove": remove, "add_late": add_late,
    "widen_single": widen_single, "split": split, "move_column": move_column,
    "shift_later": shift_later, "merge_filas": merge_filas, "drop_fila": drop_fila,
    "stack": stack, "grow": grow,
}

"""Fixed benchmark suite used to score any policy consistently across iterations.
Spans the difficulty tiers documented in Cannons/CLAUDE.md (Bajo..Extremo) plus a
random generator for broader coverage during the learning loop.
"""
from __future__ import annotations

import random

from sim.level import Level, Fila, Cuadro


def _lvl(filas_spec, number, password, hard=False) -> Level:
    filas = [Fila(cuadros=[Cuadro(index=i, tipo=t, hp=h) for (i, t, h) in fila]) for fila in filas_spec]
    return Level(levelNumber=number, password=password, isHard=hard, filas=filas)


def fixed_suite() -> list[Level]:
    """Hand-authored levels, one per difficulty tier, used as a stable regression
    check — a policy that starts losing one of these it used to win is a red flag,
    not just noise."""
    return [
        # Bajo: 1 columna, HP1
        _lvl([[(2, 1, 1)], [], [(2, 1, 1)]], 900, "L0001"),
        # Manejable: 2-3 columnas, HP2, algo de margen
        _lvl([
            [(1, 1, 1)], [(3, 1, 1)], [],
            [(2, 1, 2)], [(1, 2, 1), (3, 2, 1)],
        ], 901, "L0002"),
        # Medio: 4-5 columnas, HP3, 0-1 merges
        _lvl([
            [(0, 1, 1)], [(4, 1, 1)], [(2, 1, 2)],
            [(1, 2, 1), (3, 2, 1)], [(2, 3, 3)], [(0, 1, 2), (4, 1, 2)],
        ], 902, "L0003"),
        # Alto: 5 columnas, HP4, merges obligatorios
        _lvl([
            [(0, 1, 1)], [(1, 1, 1)], [(2, 1, 1)], [(3, 1, 1)], [(4, 1, 1)],
            [(2, 2, 4)], [(0, 2, 2), (4, 2, 2)], [(1, 3, 3), (3, 3, 3)],
        ], 903, "L0004", hard=True),
    ]


def random_level(rng: random.Random, number: int, n_filas: int = 7, difficulty: float = 0.5) -> Level:
    """Randomized level for broad-coverage benchmarking during the learning loop.
    `difficulty` in [0,1] biases hp/column-count upward; not guaranteed winnable —
    the learner is expected to discover which random draws actually are."""
    filas = []
    for _ in range(n_filas):
        n_pirates = rng.choices([0, 1, 2, 3], weights=[3, 4, 3 - difficulty, difficulty * 2])[0]
        cuadros = []
        cols = rng.sample(range(5), k=min(n_pirates, 5))
        for c in cols:
            hp = 1 + rng.choices([0, 1, 2, 3], weights=[5, 3, 2 * difficulty, difficulty])[0]
            tipo = rng.choice([1, 2, 3])
            cuadros.append(Cuadro(index=c, tipo=tipo, hp=hp))
        filas.append(Fila(cuadros=cuadros))
    return Level(levelNumber=number, password=f"R{number:04d}", isHard=difficulty > 0.6, filas=filas)


def random_suite(n: int = 20, seed: int = 0) -> list[Level]:
    rng = random.Random(seed)
    return [random_level(rng, 1000 + i, difficulty=rng.random()) for i in range(n)]

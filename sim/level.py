"""Level data model — mirrors Cannons/Assets/Scripts/Terrain/Level.cs exactly.

Cuadro.index: column, 0=derecha .. 4=izquierda
Cuadro.tipo: 0=ninguno, 1-3=pirata normal (skin only), 4=ultimo pirata del nivel, 5=reservado
Cuadro.hp: 1-10
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
import json
from pathlib import Path


@dataclass
class Cuadro:
    index: int
    tipo: int
    hp: int


@dataclass
class Fila:
    cuadros: list[Cuadro] = field(default_factory=list)


@dataclass
class Level:
    levelNumber: int
    password: str
    isHard: bool
    filas: list[Fila] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "levelNumber": self.levelNumber,
            "password": self.password,
            "isHard": self.isHard,
            "filas": [
                {"cuadros": [asdict(c) for c in fila.cuadros]}
                for fila in self.filas
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "Level":
        filas = [
            Fila(cuadros=[Cuadro(**c) for c in fila["cuadros"]])
            for fila in d.get("filas", [])
        ]
        return Level(
            levelNumber=d["levelNumber"],
            password=d["password"],
            isHard=d.get("isHard", False),
            filas=filas,
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Level":
        return Level.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def total_pirates(self) -> int:
        return sum(1 for fila in self.filas for c in fila.cuadros if c.tipo >= 1)

    def active_columns(self) -> set[int]:
        return {c.index for fila in self.filas for c in fila.cuadros if c.tipo >= 1}

    def max_hp(self) -> int:
        values = [c.hp for fila in self.filas for c in fila.cuadros if c.tipo >= 1]
        return max(values) if values else 0

    def shape_signature(self) -> str:
        """Stable hash of the level's actual challenge — fila-by-fila
        (index, hp) layout — ignoring levelNumber/password/isHard and tipo
        (tipo is cosmetic skin variety only, see Cuadro's docstring), so a
        level that's just a reskin of an existing one is still recognized as
        a duplicate. Used by production/level_registry.py to reject
        freshly-generated levels that already exist, without spending an
        extra LLM call to check."""
        import hashlib
        rows = tuple(
            tuple(sorted((c.index, c.hp) for c in fila.cuadros if c.tipo >= 1))
            for fila in self.filas
        )
        return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def pad_leading(level: Level, n: int) -> Level:
    """Copy of `level` with `n` empty filas inserted before the first one —
    shifts every wave later without changing what happens in them. Used to
    robustness-check a "confirmed" rule: if a claim about pirate/cannon
    mechanics only holds at one specific timing, it isn't a real rule (see
    2026-09-01 audit, cannonslevelgen — a whole family of "rules" turned out
    to depend on exactly how many filas came before/after the pirate being
    tested, which the real game never actually enforces as a limit)."""
    empty = [Fila(cuadros=[]) for _ in range(n)]
    return Level(
        levelNumber=level.levelNumber,
        password=level.password,
        isHard=level.isHard,
        filas=empty + list(level.filas),
    )


def pad_trailing(level: Level, n: int) -> Level:
    """Copy of `level` with `n` empty filas appended after the last one.
    See `pad_leading` — same robustness-check purpose, opposite direction."""
    empty = [Fila(cuadros=[]) for _ in range(n)]
    return Level(
        levelNumber=level.levelNumber,
        password=level.password,
        isHard=level.isHard,
        filas=list(level.filas) + empty,
    )


def empty_fila_indices(level: Level) -> list[int]:
    """Indices of filas that spawn zero pirates — a round where nothing
    happens for the player. Noticed 2026-09-15 looking at the real 500
    levels' grids: 310/500 have at least one (469 total) — dead time, not a
    deliberate design choice. See `fill_empty_filas` (used by the level
    generator at creation time) and `with_filled_fila` (used by
    verification/fill_empty_rounds.py to fix the existing 500, one fila at
    a time, each verified not to make the level worse)."""
    return [i for i, fila in enumerate(level.filas) if not any(c.tipo >= 1 for c in fila.cuadros)]


def with_filled_fila(level: Level, fila_index: int, column: int, tipo: int = 1, hp: int = 1) -> Level:
    """Copy of `level` with a single weak "distraction" pirate (hp=1 by
    default — enough to end the dead round without being a difficulty edit)
    placed into fila `fila_index`. Only ever call this on a fila that
    `empty_fila_indices` actually flagged — it overwrites whatever cuadros
    that fila already has."""
    variant = copy.deepcopy(level)
    variant.filas[fila_index] = Fila(cuadros=[Cuadro(index=column, tipo=tipo, hp=hp)])
    return variant


def fill_empty_filas(level: Level) -> Level:
    """Copy of `level` with EVERY empty fila filled via `with_filled_fila`,
    column chosen deterministically (`fila_index % NUM_COLUMNS`) so fills
    don't all land in the same column. For freshly AI-generated levels
    (production/daily_generator.py, learning/level_designer.py) — those
    already get re-simulated right after this to decide accept/reject, so
    no separate safety check is needed here; for the EXISTING 500 real
    levels, use `with_filled_fila` one fila at a time instead and verify
    each placement (see verification/fill_empty_rounds.py) before keeping
    it, since those are already-shipped/known-good levels a blind fill
    could silently break."""
    variant = copy.deepcopy(level)
    for fi in empty_fila_indices(variant):
        variant.filas[fi] = Fila(cuadros=[Cuadro(index=fi % 5, tipo=1, hp=1)])
    return variant

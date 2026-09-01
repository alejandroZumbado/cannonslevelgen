"""Level data model — mirrors Cannons/Assets/Scripts/Terrain/Level.cs exactly.

Cuadro.index: column, 0=derecha .. 4=izquierda
Cuadro.tipo: 0=ninguno, 1-3=pirata normal (skin only), 4=ultimo pirata del nivel, 5=reservado
Cuadro.hp: 1-10
"""
from __future__ import annotations

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

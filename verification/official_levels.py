"""Loads the 500 real shipped Cannons levels (Cannons/Assets/Levels/*.asset)
directly into sim.level.Level, so the weekly audit plays the ACTUAL game
content — not the synthetic random/fixed benchmark suites in sim/benchmark.py
used to score policy candidates during learning.

Unity .asset files are plain YAML (ScriptableObject serialization) with a
Unity-specific header (`%YAML`, `%TAG`) and document marker
(`--- !u!114 &...`) that a standard YAML parser chokes on — stripped here
before parsing, since everything below the marker (the MonoBehaviour
mapping) is ordinary YAML. Verified 2026-09-10 against all 500 real files in
the repo: 0 parse failures, every levelNumber 1..500 present exactly once.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from sim.level import Cuadro, Fila, Level

ASSET_GLOB = "Level_*.asset"
# The database index file matches the glob but isn't a level — has no
# levelNumber/filas of its own, just an ordered list of references to the
# other assets.
_EXCLUDE_NAMES = {"LevelDatabase.asset"}


def _strip_unity_header(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("%") and not line.startswith("---")
    )


def parse_asset_file(path: Path) -> Level:
    doc = yaml.safe_load(_strip_unity_header(path.read_text(encoding="utf-8")))
    mb = doc["MonoBehaviour"]
    filas = [
        Fila(cuadros=[Cuadro(index=c["index"], tipo=c["tipo"], hp=c["hp"])
                      for c in (fila.get("cuadros") or [])])
        for fila in (mb.get("filas") or [])
    ]
    return Level(
        levelNumber=mb["levelNumber"],
        password=mb["password"],
        isHard=bool(mb.get("isHard", False)),
        filas=filas,
    )


def load_all(levels_dir: Path) -> list[Level]:
    """Returns all levels found in `levels_dir`, sorted by levelNumber.
    Raises if any two files claim the same levelNumber (would silently
    shadow one level's audit result with another's) or if the folder is
    missing entirely (audit should fail loudly, not report on 0 levels)."""
    if not levels_dir.is_dir():
        raise FileNotFoundError(f"Cannons levels folder not found: {levels_dir}")

    files = [f for f in sorted(levels_dir.glob(ASSET_GLOB)) if f.name not in _EXCLUDE_NAMES]
    if not files:
        raise FileNotFoundError(f"No {ASSET_GLOB} files found in {levels_dir}")

    levels: dict[int, Level] = {}
    for f in files:
        level = parse_asset_file(f)
        if level.levelNumber in levels:
            raise ValueError(
                f"Duplicate levelNumber {level.levelNumber}: {f.name} collides with an earlier file"
            )
        levels[level.levelNumber] = level

    return [levels[n] for n in sorted(levels)]

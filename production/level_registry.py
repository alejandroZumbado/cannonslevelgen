"""Reads the REAL state of Cannons/Assets/Levels — not a separately-tracked
counter that could drift — to figure out the next sane level number and
avoid password collisions.

Found the hard way: the first real cloud run of daily_generator.py used
datetime.now().toordinal() as the level number (no better source was wired
up at the time) and pushed a level numbered 739851 into the actual game
repo, while Assets/Levels/ already had Level_001..Level_500 sequentially.
Scanning the real .asset files directly is the only source of truth that
can't get out of sync with what's actually in the game.
"""
from __future__ import annotations

import re
import string
from pathlib import Path

_LEVEL_NUMBER_RE = re.compile(r"^\s*levelNumber:\s*(\d+)", re.MULTILINE)
_PASSWORD_RE = re.compile(r"^\s*password:\s*(\S+)", re.MULTILINE)


def scan_existing_levels(cannons_repo_path: Path) -> tuple[int, set[str]]:
    """Returns (highest levelNumber found, set of every password in use).
    Scans BOTH Assets/Levels/*.asset (already-imported levels) and any
    leftover GeneratedLevels/incoming|processed/*.json (generated but maybe
    not yet imported) so a same-day rerun or an import lag doesn't reuse a
    number/password that's already spoken for."""
    max_number = 0
    passwords: set[str] = set()

    assets_dir = cannons_repo_path / "Assets" / "Levels"
    for asset_file in assets_dir.glob("*.asset") if assets_dir.exists() else []:
        text = asset_file.read_text(encoding="utf-8", errors="ignore")
        for m in _LEVEL_NUMBER_RE.finditer(text):
            max_number = max(max_number, int(m.group(1)))
        for m in _PASSWORD_RE.finditer(text):
            passwords.add(m.group(1).strip())

    for sub in ("incoming", "processed"):
        gen_dir = cannons_repo_path / "GeneratedLevels" / sub
        if not gen_dir.exists():
            continue
        for json_file in gen_dir.glob("*.json"):
            text = json_file.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'"levelNumber"\s*:\s*(\d+)', text)
            if m:
                max_number = max(max_number, int(m.group(1)))
            m = re.search(r'"password"\s*:\s*"([^"]+)"', text)
            if m:
                passwords.add(m.group(1).strip())

    return max_number, passwords


def next_level_number(cannons_repo_path: Path) -> int:
    max_number, _ = scan_existing_levels(cannons_repo_path)
    return max_number + 1


def ensure_unique_password(password: str, used: set[str], rng=None) -> str:
    """Returns `password` unchanged if it's not already used, otherwise a
    freshly generated one in the same format (1 uppercase letter + 4 digits)
    guaranteed not to collide with `used`."""
    import random
    rng = rng or random.Random()

    if password not in used:
        return password

    for _ in range(1000):
        candidate = rng.choice(string.ascii_uppercase) + "".join(rng.choices(string.digits, k=4))
        if candidate not in used:
            return candidate
    raise RuntimeError("could not find a free password after 1000 tries — used-password set is implausibly large")

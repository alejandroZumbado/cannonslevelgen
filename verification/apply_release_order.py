"""Writes the curated release order (reports/release_order.json, from
verification/curate_release.py) into the real game's
Assets/Levels/LevelDatabase.asset, so levels[0..199] are exactly the
release in order.

Only the `levels:` list of LevelDatabase.asset is rewritten (surgical text
edit, same `- {fileID: 11400000, guid: ..., type: 2}` lines Unity writes) —
no YAML re-dump, so the rest of the file stays byte-identical. Level
.asset files themselves are never touched: levels left out of the release
stay on disk as the reserve pool and keep being audited.

Run standalone: `python -m verification.apply_release_order`
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import config
from verification.official_levels import parse_asset_file

ORDER_PATH = config.ROOT / "reports" / "release_order.json"
LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
DB_PATH = LEVELS_DIR / "LevelDatabase.asset"

_GUID_RE = re.compile(r"^guid: ([0-9a-f]{32})\s*$", re.MULTILINE)
_REF_LINE = "  - {{fileID: 11400000, guid: {guid}, type: 2}}"


def _guid_of(asset: Path) -> str:
    # Unity keeps an asset's GUID in its .meta file; that's what the
    # database references, not the path.
    meta = asset.with_name(asset.name + ".meta")
    match = _GUID_RE.search(meta.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(f"no guid in {meta}")
    return match.group(1)


def _guids_by_level_number() -> dict[int, str]:
    out: dict[int, str] = {}
    for asset in sorted(LEVELS_DIR.glob("*.asset")):
        if asset.name == DB_PATH.name:
            continue
        n = parse_asset_file(asset).levelNumber
        if n in out:
            raise RuntimeError(f"two assets claim levelNumber {n}")  # same guard as load_all
        out[n] = _guid_of(asset)
    return out


def rewrite_levels_list(db_text: str, guids: list[str]) -> str:
    """Replaces the block of `  - {fileID...}` lines right after `  levels:`.
    Raises if the file doesn't have exactly that shape, rather than
    guessing where the list is."""
    lines = db_text.split("\n")
    try:
        start = lines.index("  levels:") + 1
    except ValueError:
        raise RuntimeError("LevelDatabase.asset has no '  levels:' line")
    end = start
    while end < len(lines) and lines[end].startswith("  - {fileID:"):
        end += 1
    if end == start:
        raise RuntimeError("LevelDatabase.asset 'levels:' list is empty or in an unexpected format")
    return "\n".join(lines[:start] + [_REF_LINE.format(guid=g) for g in guids] + lines[end:])


def main() -> None:
    order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))["order"]
    by_number = _guids_by_level_number()
    missing = [o["levelNumber"] for o in order if o["levelNumber"] not in by_number]
    if missing:
        raise SystemExit(f"release references levels with no .asset: {missing}")

    guids = [by_number[o["levelNumber"]] for o in order]
    # newline="" keeps Unity's original line endings untouched.
    text = DB_PATH.read_text(encoding="utf-8", newline="")
    new_text = rewrite_levels_list(text, guids)
    DB_PATH.write_text(new_text, encoding="utf-8", newline="")

    # Read back and confirm the file now lists exactly the release, in order.
    written = re.findall(r"^  - \{fileID: 11400000, guid: ([0-9a-f]{32}), type: 2\}",
                         DB_PATH.read_text(encoding="utf-8"), re.MULTILINE)
    if written != guids:
        raise SystemExit("verification failed: LevelDatabase.asset doesn't match the release order")
    print(f"LevelDatabase.asset now holds {len(guids)} levels in release order "
          f"(first: level {order[0]['levelNumber']}, last: level {order[-1]['levelNumber']}).")


if __name__ == "__main__":
    main()

"""Writes the curated release order (reports/release_order.json, from
verification/curate_release.py) into the real game's
Assets/Levels/LevelDatabase.asset, so levels[0..N-1] are exactly the
release in order.

Also sets each release level's `isHard` from its arc role (2026-09-24):
isHard (the hard-level music) = the arc's `peak`, nothing else — so the
music marks the "boss" level that closes every ~11-level arc. Before, the
flag came from each level's original authoring and didn't match the curve
(33 flagged, only 8 of them peaks). This is a one-line surgical edit of
`  isHard: 0|1` in the level .asset; reserve levels are never touched.

Only the `levels:` list of LevelDatabase.asset is rewritten (surgical text
edit, same `- {fileID: 11400000, guid: ..., type: 2}` lines Unity writes) —
no YAML re-dump, so the rest of the file stays byte-identical. Levels left
out of the release stay on disk untouched as the reserve pool and keep
being audited.

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


# lookahead (not \s*$): assets are CRLF and \s would swallow the \r on substitution
_IS_HARD_RE = re.compile(r"^  isHard: [01](?=\r?$)", re.MULTILINE)


def _assets_by_level_number() -> dict[int, Path]:
    out: dict[int, Path] = {}
    for asset in sorted(LEVELS_DIR.glob("*.asset")):
        if asset.name == DB_PATH.name:
            continue
        n = parse_asset_file(asset).levelNumber
        if n in out:
            raise RuntimeError(f"two assets claim levelNumber {n}")  # same guard as load_all
        out[n] = asset
    return out


def set_is_hard(asset: Path, is_hard: bool) -> bool:
    """Rewrites the `  isHard:` line of one level asset. Returns True if the
    value changed. Raises if the line isn't there exactly once, rather than
    guessing (same policy as rewrite_levels_list)."""
    text = asset.read_text(encoding="utf-8", newline="")
    if len(_IS_HARD_RE.findall(text)) != 1:
        raise RuntimeError(f"{asset.name}: expected exactly one '  isHard: 0|1' line")
    new_text = _IS_HARD_RE.sub(f"  isHard: {int(is_hard)}", text, count=1)
    if new_text == text:
        return False
    asset.write_text(new_text, encoding="utf-8", newline="")
    return True


def rewrite_levels_list(db_text: str, guids: list[str]) -> str:
    """Replaces the block of `  - {fileID...}` lines right after `  levels:`.
    Raises if the file doesn't have exactly that shape, rather than
    guessing where the list is."""
    # Windows checkout (core.autocrlf=true) gives CRLF: split on the file's own
    # newline so "  levels:" matches and the rewrite keeps the same endings.
    newline = "\r\n" if "\r\n" in db_text else "\n"
    lines = db_text.split(newline)
    try:
        start = lines.index("  levels:") + 1
    except ValueError:
        raise RuntimeError("LevelDatabase.asset has no '  levels:' line")
    end = start
    while end < len(lines) and lines[end].startswith("  - {fileID:"):
        end += 1
    if end == start:
        raise RuntimeError("LevelDatabase.asset 'levels:' list is empty or in an unexpected format")
    return newline.join(lines[:start] + [_REF_LINE.format(guid=g) for g in guids] + lines[end:])


def main() -> None:
    order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))["order"]
    assets = _assets_by_level_number()
    missing = [o["levelNumber"] for o in order if o["levelNumber"] not in assets]
    if missing:
        raise SystemExit(f"release references levels with no .asset: {missing}")

    guids = [_guid_of(assets[o["levelNumber"]]) for o in order]
    # newline="" keeps Unity's original line endings untouched.
    text = DB_PATH.read_text(encoding="utf-8", newline="")
    new_text = rewrite_levels_list(text, guids)
    DB_PATH.write_text(new_text, encoding="utf-8", newline="")

    # Read back and confirm the file now lists exactly the release, in order.
    written = re.findall(r"^  - \{fileID: 11400000, guid: ([0-9a-f]{32}), type: 2\}",
                         DB_PATH.read_text(encoding="utf-8"), re.MULTILINE)
    if written != guids:
        raise SystemExit("verification failed: LevelDatabase.asset doesn't match the release order")
    # hard-level music = arc peaks only (see module docstring)
    changed = sum(set_is_hard(assets[o["levelNumber"]], o["role"] == "peak") for o in order)
    peaks = sum(o["role"] == "peak" for o in order)
    print(f"isHard: {peaks} arc peaks flagged, {changed} level asset(s) changed.")

    print(f"LevelDatabase.asset now holds {len(guids)} levels in release order "
          f"(first: level {order[0]['levelNumber']}, last: level {order[-1]['levelNumber']}).")


if __name__ == "__main__":
    main()

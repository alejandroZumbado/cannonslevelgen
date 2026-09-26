"""Applies the regulator's results (reports/regulation/<pass>/shard_*.json,
see verification/regulator.py) to the real game's level assets.

Only levels whose record carries a new layout AND ended winnable are written
(status regulated / repaired / repaired_partial / improved_partial);
"unresolved" and "error" levels keep their current asset untouched, so this
can never make a level worse than the simulation proved.

Surgical text edit, same policy as apply_release_order.py: only the `filas:`
block (always the last key of a Level asset) is rewritten, in Unity's own
YAML layout and line endings; every written file is parsed back and compared
field by field — any mismatch aborts before anything is pushed.

A regulated level with no asset yet (Level 508 lived only as rejected JSON)
gets a new Level_###_generated.asset + .meta, with a GUID derived from its
levelNumber so re-runs are idempotent.

Run: `python -m verification.apply_regulation --pass pass1 [--push]`
(--push commits+pushes the Cannons checkout; used by level_regulation.yml).
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from production.cannons_sync import push_paths
from sim.level import Level
from verification.official_levels import ASSET_GLOB, parse_asset_file

LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
REPORT_ROOT = config.ROOT / "reports" / "regulation"
APPLY_STATUSES = {"regulated", "repaired", "repaired_partial", "improved_partial", "llm_proposal"}
PROPOSALS_PATH = REPORT_ROOT / "llm_proposals.json"  # learning/regulation_designer.py
TEMPLATE_ASSET = "Level_001.asset"  # header donor for brand-new assets
META_TEMPLATE = """fileFormatVersion: 2
guid: {guid}
NativeFormatImporter:
  externalObjects: {{}}
  mainObjectFileID: 11400000
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def load_results(pass_name: str) -> dict[int, dict]:
    """All shard files of one pass merged. Missing pass = explicit error."""
    shard_files = sorted((REPORT_ROOT / pass_name).glob("shard_*.json"))
    if not shard_files:
        raise SystemExit(f"no results in {REPORT_ROOT / pass_name} — run the regulator first")
    merged: dict[int, dict] = {}
    for path in shard_files:
        for record in json.loads(path.read_text(encoding="utf-8"))["levels"]:
            merged[record["levelNumber"]] = record
    return merged


def merge_llm_proposals(results: dict[int, dict]) -> list[str]:
    """LLM redesigns (verified strictly better than the regulator's version
    when proposed) override that level's record — in EVERY pass, even once
    already applied: finalize re-applies pass 2 after pass 1, and skipping
    applied proposals there let pass 2's older record overwrite them (would
    have reverted 508 to its 18-round version, found 2026-09-26). Re-writing
    the same proposal is idempotent. Returns the levelNumbers used."""
    if not PROPOSALS_PATH.exists():
        return []
    used = []
    for number, proposal in json.loads(PROPOSALS_PATH.read_text(encoding="utf-8")).items():
        results[int(number)] = {"levelNumber": int(number), "status": "llm_proposal",
                                "level": proposal["level"], "after": proposal["after"]}
        used.append(number)
    return used


def _mark_proposals_applied(numbers: list[str], pass_name: str) -> None:
    proposals = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    for number in numbers:
        # keeps the FIRST pass that applied it (the later re-applies are no-ops)
        proposals[number].setdefault("applied_in", pass_name)
    PROPOSALS_PATH.write_text(json.dumps(proposals, indent=1, ensure_ascii=False), encoding="utf-8")


def render_filas(level: Level, newline: str) -> str:
    """The `filas:` block exactly as Unity serializes it."""
    lines = ["  filas:"]
    for fila in level.filas:
        lines.append("  - cuadros:")
        for c in fila.cuadros:
            lines += [f"    - index: {c.index}", f"      tipo: {c.tipo}", f"      hp: {c.hp}"]
    return newline.join(lines) + newline


def replace_filas(asset_text: str, level: Level) -> str:
    newline = "\r\n" if "\r\n" in asset_text else "\n"
    marker = f"{newline}  filas:"
    cut = asset_text.find(marker)
    if cut < 0:
        raise RuntimeError("asset has no '  filas:' block")
    return asset_text[:cut + len(newline)] + render_filas(level, newline)


def _asset_paths_by_number() -> dict[int, Path]:
    out = {}
    for path in LEVELS_DIR.glob(ASSET_GLOB):
        if path.name == "LevelDatabase.asset":
            continue
        out[parse_asset_file(path).levelNumber] = path
    return out


def _new_asset_text(level: Level, name: str) -> str:
    """Header copied from an existing level asset (same script GUID), with
    this level's own name/number/password."""
    template = (LEVELS_DIR / TEMPLATE_ASSET).read_text(encoding="utf-8", newline="")
    newline = "\r\n" if "\r\n" in template else "\n"
    head = template[: template.find(f"{newline}  filas:") + len(newline)]
    out = []
    for line in head.split(newline):
        key = line.strip().split(":")[0]
        if key == "m_Name":
            line = f"  m_Name: {name}"
        elif key == "levelNumber":
            line = f"  levelNumber: {level.levelNumber}"
        elif key == "password":
            line = f"  password: {level.password}"
        elif key == "isHard":
            line = f"  isHard: {int(level.isHard)}"
        out.append(line)
    return newline.join(out) + render_filas(level, newline)


def _verify(path: Path, expected: Level) -> None:
    got = parse_asset_file(path)
    if got.to_dict() != expected.to_dict():
        raise RuntimeError(f"{path.name}: parsed back != regulated level — aborting before any push")


def apply(results: dict[int, dict]) -> tuple[list[Path], dict[str, int]]:
    assets = _asset_paths_by_number()
    written: list[Path] = []
    counts: dict[str, int] = {}
    for number, record in sorted(results.items()):
        status = record.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        if status not in APPLY_STATUSES or "level" not in record:
            continue
        level = Level.from_dict(record["level"])
        if number in assets:
            path = assets[number]
            level.isHard = parse_asset_file(path).isHard  # isHard belongs to curation, keep it
            text = path.read_text(encoding="utf-8", newline="")
            path.write_text(replace_filas(text, level), encoding="utf-8", newline="")
        else:
            name = f"Level_{number:03d}_generated"
            path = LEVELS_DIR / f"{name}.asset"
            path.write_text(_new_asset_text(level, name), encoding="utf-8", newline="")
            meta = path.with_name(path.name + ".meta")
            guid = uuid.uuid5(uuid.NAMESPACE_URL, f"cannons-level-{number}").hex
            meta.write_text(META_TEMPLATE.format(guid=guid), encoding="utf-8")
            written.append(meta)
        _verify(path, level)
        written.append(path)
    return written, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply regulator results to Cannons level assets.")
    parser.add_argument("--pass", dest="pass_name", default="pass1")
    parser.add_argument("--push", action="store_true", help="commit+push the Cannons checkout")
    args = parser.parse_args(argv)

    results = load_results(args.pass_name)
    llm_used = merge_llm_proposals(results)
    written, counts = apply(results)
    if llm_used:
        _mark_proposals_applied(llm_used, args.pass_name)
    print(f"regulation {args.pass_name}: {len(results)} levels in results {counts}; "
          f"{len(written)} files written and verified")
    (REPORT_ROOT / args.pass_name / "applied.json").write_text(json.dumps({
        "counts": counts, "files": [p.name for p in written]}, indent=1), encoding="utf-8")

    if args.push and written:
        ok = push_paths(config.CANNONS_REPO, [LEVELS_DIR],
                        f"[bot] Regulate levels ({args.pass_name}): {counts}")
        if not ok:
            print("push to Cannons failed — see the git message above")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""ONE command to append a new batch of levels to the game's release.

    python -m verification.extend_release --count 100 [--dry-run] [--allow-fewer]

Steps (each fails loudly, nothing half-applied on error):
  1. Refuses if Cannons/GeneratedLevels/incoming/ still has JSON: those
     aren't Unity assets yet — import them first (Unity: Levels > Import
     Generated Levels, or headless LevelImporter.ImportGeneratedLevels).
  2. Refreshes the official audit if any level asset is new or changed
     since the last one (classification + difficulty_score are needed to
     place levels). Does NOT commit — see level_audit.run_and_save.
  3. Rebuilds the campaign manifest (keeps every existing assignment).
  4. Picks `count` ready levels and appends them after the last position,
     in difficulty arcs (curate_release.extend). Existing positions never
     move, so players' saves stay valid.
  5. Writes manifest + release_order.json and rewrites Cannons'
     LevelDatabase.asset (apply_release_order).
  6. Runs Unity's ReleaseValidator headless if Unity is available and the
     project isn't open in the Editor.

--dry-run stops after step 4 and only prints the plan.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import config
from verification import apply_release_order, campaign_manifest, curate_release, level_audit
from verification.official_levels import load_all

LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
INCOMING_DIR = config.CANNONS_REPO / "GeneratedLevels" / "incoming"


def _check_no_pending_imports() -> None:
    pending = sorted(INCOMING_DIR.glob("*.json")) if INCOMING_DIR.is_dir() else []
    if pending:
        raise SystemExit(
            f"{len(pending)} level JSON(s) still in {INCOMING_DIR} — import them into Unity first "
            f"(Levels > Import Generated Levels (JSON)), then re-run. First: {pending[0].name}")


def _audit_is_stale() -> tuple[bool, str]:
    """Stale if an asset levelNumber is missing from the audit, or any level
    asset was modified after the audit ran (e.g. edited in the Grid Editor)."""
    if not level_audit.LATEST_PATH.exists():
        return True, "no audit yet"
    audit = json.loads(level_audit.LATEST_PATH.read_text(encoding="utf-8"))
    audited = {lvl["levelNumber"] for lvl in audit["levels"]}
    current = {lvl.levelNumber for lvl in load_all(LEVELS_DIR)}
    missing = current - audited
    if missing:
        return True, f"{len(missing)} level(s) not audited yet, e.g. {sorted(missing)[:5]}"
    audited_at = datetime.fromisoformat(audit["generated_at"]).timestamp()
    newer = [p.name for p in LEVELS_DIR.glob("*.asset")
             if p.name != "LevelDatabase.asset" and p.stat().st_mtime > audited_at]
    if newer:
        return True, f"{len(newer)} level asset(s) changed since the audit, e.g. {newer[:3]}"
    return False, "audit is current"


def _print_plan(new: list[dict], stats: dict) -> None:
    print(f"\nPlan: {len(new)} level(s) -> positions {new[0]['order']}..{new[-1]['order']}")
    arc = None
    for o in new:
        if o["arc"] != arc:
            arc = o["arc"]
            print(f"\n  Arc {arc}:", end="")
        tag = {"peak": "*", "breather": "~"}.get(o["role"], "")
        print(f" {tag}{o['levelNumber']}({o['difficulty_score']})", end="")
    print(f"\n\n  (~ = breather, * = peak)  reskins excluded: {stats['duplicate_shapes_excluded']}")
    print(f"  ready levels left in reserve after this: {len(stats['reserve'])}")


def _run_unity_validator() -> None:
    unity = Path(config.UNITY_EXE)
    if not unity.exists():
        print(f"\nUnity not found at {unity} (set UNITY_EXE) — run Levels > Validate Release in the Editor.")
        return
    # temp dir, not the repo: the bots' `git add -A` would commit it
    log = Path(tempfile.gettempdir()) / "cannons_unity_validate.log"
    print("\nRunning Unity ReleaseValidator headless (1-2 min)...")
    code = subprocess.call([str(unity), "-batchmode", "-projectPath", str(config.CANNONS_REPO),
                            "-executeMethod", "ReleaseValidator.RunHeadless", "-logFile", str(log)])
    lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
             if "[ReleaseValidator]" in line] if log.exists() else []
    for line in dict.fromkeys(lines):  # unique, in order
        print("  " + line.strip())
    if code != 0:
        if not lines:
            print(f"  Unity exited {code} without validator output — probably the project is open "
                  f"in the Editor. Close it and run Levels > Validate Release, or see {log}.")
        raise SystemExit(f"Unity validation failed (exit {code}). The release WAS written; "
                         f"fix the errors above or revert with git in both repos.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Append a batch of levels to the release.")
    parser.add_argument("--count", type=int, required=True, help="how many levels to add")
    parser.add_argument("--allow-fewer", action="store_true",
                        help="add as many as the ready pool allows instead of failing")
    parser.add_argument("--dry-run", action="store_true", help="only print the plan")
    parser.add_argument("--skip-unity", action="store_true", help="don't run the Unity validator")
    args = parser.parse_args(argv)

    _check_no_pending_imports()

    stale, why = _audit_is_stale()
    if stale:
        print(f"Audit refresh needed ({why}) — running it (~8 min for 500 levels)...")
        level_audit.run_and_save()

    manifest = campaign_manifest.build_manifest()
    existing = curate_release.load_existing_order()
    if not existing:
        raise SystemExit("No release yet — run `python -m verification.curate_release` first.")

    try:
        new, stats = curate_release.extend(manifest, existing, args.count, allow_fewer=args.allow_fewer)
    except curate_release.NotEnoughLevels as e:
        raise SystemExit(f"Not enough levels: {e}")

    _print_plan(new, stats)
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    counts = curate_release.save(manifest, existing + new, stats, batch_size=len(new))
    print(f"\nManifest pools now: {counts}")
    apply_release_order.main()
    if not args.skip_unity:
        _run_unity_validator()
    print("\nDone. Review and commit BOTH repos: Cannons (LevelDatabase.asset) and "
          "cannonslevelgen (reports/). Play-test the new positions in the Editor.")


if __name__ == "__main__":
    sys.exit(main())

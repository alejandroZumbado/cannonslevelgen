"""Final stage of the regulation workflow (.github/workflows/level_regulation.yml),
run once the pass-1 shards are applied to the game:

  1. re-audit every level in the Cannons checkout (champion + wide solver):
     "a bot tries to win them all again";
  2. regulator pass 2 over ALL levels: anything still broken or with empty
     rounds gets repaired, everything fine is kept (fast);
  3. apply pass 2 to the assets;
  4. re-audit, rebuild the manifest and the WHOLE campaign order (every
     winnable level, arcs; see curate_release.rebuild_all), write
     LevelDatabase + isHard (apply_release_order);
  5. variety report; push the Cannons checkout; commit this repo's reports.

Each step prints what it did; any step raising stops the run before the push,
so a half-finished state never reaches the game repo.

Run: `python -m verification.regulation_finalize [--push]`
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
import git_sync
from production.cannons_sync import push_paths
from verification import (apply_regulation, apply_release_order, campaign_manifest, curate_release,
                          level_audit, regulator, variety_report)


def _step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def _rebuild_campaign() -> None:
    sys_argv = sys.argv
    sys.argv = [sys_argv[0], "--rebuild-all"]  # curate_release reads its flag from argv
    try:
        curate_release.main()
    finally:
        sys.argv = sys_argv


def main() -> int:
    push = "--push" in sys.argv

    _step("1. re-audit every level (champion + solver)")
    report = level_audit.run_and_save()
    print(f"audit: {report['counts']}")

    _step("2. regulator pass 2 — repair anything still broken / empty")
    regulator.main(["--shard", "0", "--shards", "1", "--pass-name", "pass2"] + ([] if push else ["--no-push"]))

    _step("3. apply pass 2 to the assets")
    apply_regulation.main(["--pass", "pass2"])

    _step("4. re-audit, manifest, rebuild the whole campaign, LevelDatabase + isHard")
    report = level_audit.run_and_save()
    print(f"audit after repairs: {report['counts']}")
    campaign_manifest.main()
    _rebuild_campaign()
    apply_release_order.main()

    _step("5. variety report + push")
    variety_report.main()
    if push:
        levels_dir = config.CANNONS_REPO / "Assets" / "Levels"
        if not push_paths(config.CANNONS_REPO, [levels_dir],
                          f"[bot] Regulated campaign: {report['counts']} — whole campaign re-curated"):
            print("Cannons push failed or nothing to push — see the git message above")
        git_sync.commit_and_push(f"[bot] regulation finalize - audit {report['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Release gate: "if everything auto-deployed now, what would break?"

    python -m verification.release_gate [--fast] [--no-live] [--snapshot-deployed]

Read-only bot (2026-09-26). It never builds, pushes or deploys; it answers
GO / HOLD / BLOCK for shipping the current LevelDatabase to WebGL/stores:

  levels    every release level replayed NOW (champion, then solver) —
            unwinnable, out-of-range, duplicate id/password = BLOCK;
            pacing problems = warning.
  batches   the release only grows in blocks of BATCH_SIZE (100) appended
            after what's already deployed; already-deployed positions must not
            move (warning before the game is published, BLOCK after).
  live      the published WebGL page and its Build/ files answer 200 and
            match the local build; a build older than the last LevelDatabase
            change = the site is serving stale levels (warning).
  pipeline  scheduled bots: repeated workflow failures (warning).
  forecast  next 7 days: levels the daily bot is likely to add, and how far
            the ready reserve is from the next batch of 100.

Writes reports/release_gate/latest.{json,md}. Exit code: 0 GO, 2 HOLD, 1 BLOCK.
--snapshot-deployed records the current release as "what's live" — run it
right after a real deploy (the deploy script should call it).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from verification import level_audit, pacing
from verification.official_levels import load_all, parse_asset_file
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BATCH_SIZE = 100
# Flip to True the day the game ships on a store: from then on a moved
# position breaks players' saves, so it becomes a BLOCK instead of a warning.
GAME_PUBLISHED = False

LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
DB_PATH = LEVELS_DIR / "LevelDatabase.asset"
BUILD_REPO = config.ROOT.parent / "Builds" / "Cannons"   # cannons-build clone (WebGL output)
LIVE_URL = "https://alejandrozumbado.github.io/cannons-build/"
REPORT_DIR = config.ROOT / "reports" / "release_gate"
DEPLOYED_PATH = REPORT_DIR / "deployed.json"
WORKFLOWS = ("learning.yml", "daily_production.yml", "weekly_level_audit.yml")
PASSWORD_RE = re.compile(r"^[A-Z]\d{4}$")          # CLAUDE.md: 1 letter + 4 digits
_DB_GUID_RE = re.compile(r"^  - \{fileID: 11400000, guid: ([0-9a-f]{32}), type: 2\}", re.MULTILINE)
_META_GUID_RE = re.compile(r"^guid: ([0-9a-f]{32})\s*$", re.MULTILINE)


class Findings:
    """Collects errors (BLOCK), holds (HOLD) and warnings per section."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, severity: str, section: str, message: str) -> None:
        self.items.append({"severity": severity, "section": section, "message": message})

    def verdict(self) -> str:
        severities = {i["severity"] for i in self.items}
        return "BLOCK" if "error" in severities else "HOLD" if "hold" in severities else "GO"


# ---------------------------------------------------------------- release ---

def _guid_to_asset() -> dict[str, Path]:
    """GUID (from each .meta) -> level asset, the key LevelDatabase uses."""
    out = {}
    for asset in LEVELS_DIR.glob("*.asset"):
        if asset == DB_PATH:
            continue
        match = _META_GUID_RE.search(asset.with_name(asset.name + ".meta").read_text(encoding="utf-8"))
        if match:
            out[match.group(1)] = asset
    return out


def release_level_numbers(db_text: str, guid_map: dict[str, Path]) -> list[int]:
    """levelNumbers in release order. A GUID with no asset raises — that
    would be a null entry in the shipped game."""
    numbers = []
    for guid in _DB_GUID_RE.findall(db_text):
        if guid not in guid_map:
            raise RuntimeError(f"LevelDatabase references guid {guid} with no level asset")
        numbers.append(parse_asset_file(guid_map[guid]).levelNumber)
    return numbers


# ----------------------------------------------------------------- levels ---

def _winnability(levels: list, fast: bool) -> dict[int, str]:
    """levelNumber -> classification. --fast reuses the weekly audit when it
    is newer than every asset; otherwise every level is replayed now."""
    if fast and level_audit.LATEST_PATH.exists():
        audit = json.loads(level_audit.LATEST_PATH.read_text(encoding="utf-8"))
        audited_at = datetime.fromisoformat(audit["generated_at"]).timestamp()
        if all(p.stat().st_mtime <= audited_at for p in LEVELS_DIR.glob("*.asset") if p != DB_PATH):
            return {lv["levelNumber"]: lv["classification"] for lv in audit["levels"]}
        print("  --fast ignored: level assets changed since the last audit, replaying all")
    from policy.loader import load_policy_from_file
    champion = load_policy_from_file(config.ROOT / "policy" / "current.py")
    return {lv.levelNumber: level_audit.audit_level(lv, champion, ESCALATION_BEAM_WIDTHS,
                                                     DEFAULT_MAX_ROUNDS).classification
            for lv in levels}


def check_levels(release: list, findings: Findings, fast: bool) -> dict:
    """Everything that would make a shipped level broken or unfair."""
    numbers = Counter(lv.levelNumber for lv in release)
    passwords = Counter(lv.password.strip().upper() for lv in release)
    for n, c in numbers.items():
        if c > 1:
            findings.add("error", "levels", f"levelNumber {n} appears {c} times in the release")
    for pw, c in passwords.items():
        if c > 1:
            findings.add("error", "levels", f"password {pw} shared by {c} release levels")

    classes = _winnability(release, fast)
    pacing_bad = []
    for pos, lv in enumerate(release, start=1):
        tag = f"position {pos} (level {lv.levelNumber})"
        for err in lv.structure_errors():
            findings.add("error", "levels", f"{tag}: {err}")
        if not PASSWORD_RE.match(lv.password.strip().upper()):
            findings.add("warning", "levels", f"{tag}: password '{lv.password}' isn't letter+4 digits")
        if any(not f.cuadros for f in lv.filas):
            findings.add("error", "levels", f"{tag}: has a round with no pirates")
        cls = classes.get(lv.levelNumber, "not_audited")
        if cls not in ("champion_win", "solved_by_search_only"):
            findings.add("error", "levels", f"{tag}: {cls} — players could get stuck here")
        if not pacing.pacing_report(lv).ok:
            pacing_bad.append(lv.levelNumber)
    if pacing_bad:
        findings.add("warning", "levels", f"{len(pacing_bad)} release levels fail the pacing gate "
                                          f"(breathers are expected to), e.g. {pacing_bad[:8]}")
    return {"count": len(release), "classifications": dict(Counter(classes.get(lv.levelNumber)
                                                                     for lv in release)),
            "pacing_failures": len(pacing_bad)}


# ---------------------------------------------------------------- batches ---

def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout


def derive_deployed_from_git(guid_map: dict[str, Path]) -> dict:
    """What the live WebGL build contains, when no snapshot exists yet: the
    LevelDatabase committed in Cannons just before the last cannons-build
    commit (the build packs the levels into its .data file)."""
    built_at = _git(BUILD_REPO, "log", "-1", "--format=%cI").strip()
    source = _git(config.CANNONS_REPO, "rev-list", "-1", f"--before={built_at}", "HEAD").strip()
    db_text = _git(config.CANNONS_REPO, "show", f"{source}:Assets/Levels/LevelDatabase.asset")
    return {"deployed_at": built_at, "source_commit": source, "derived_from_git": True,
            "levels": release_level_numbers(db_text, guid_map)}


def check_batches(current: list[int], deployed: dict, findings: Findings) -> dict:
    """Append-only growth in blocks of BATCH_SIZE on top of what's live."""
    live = deployed["levels"]
    moved = [i + 1 for i, n in enumerate(live) if i >= len(current) or current[i] != n]
    if moved:
        msg = (f"{len(moved)} of {len(live)} already-deployed positions changed level "
               f"(first: position {moved[0]}) — players' saved progress would point elsewhere")
        findings.add("error" if GAME_PUBLISHED else "warning", "batches",
                     msg + ("" if GAME_PUBLISHED else " (allowed only because the game isn't published)"))
    new = len(current) - len(live)
    if new < 0:
        findings.add("error", "batches", f"release shrank from {len(live)} to {len(current)} levels")
    elif new == 0 and not moved:
        findings.add("hold", "batches", "nothing new to ship since the last deploy")
    elif new % BATCH_SIZE:
        findings.add("hold", "batches", f"{new} new levels since the last deploy — not a multiple of "
                                        f"{BATCH_SIZE}; ship {new // BATCH_SIZE * BATCH_SIZE} or wait "
                                        f"for {BATCH_SIZE - new % BATCH_SIZE} more")
    return {"deployed": len(live), "current": len(current), "new": new, "moved_positions": len(moved),
            "deployed_at": deployed.get("deployed_at"), "snapshot_derived": deployed.get("derived_from_git", False)}


# ------------------------------------------------------------------- live ---

def _local_build_files() -> dict[str, int]:
    build = BUILD_REPO / "Build"
    return {p.name: p.stat().st_size for p in build.iterdir()} if build.exists() else {}


def check_live(findings: Findings) -> dict:
    """The published page and each Build/ file it loads answer 200 and have
    the same size as the local build (catches half-pushed / cached deploys)."""
    import requests  # only this section needs the network
    out = {"url": LIVE_URL, "files": {}}
    try:
        page = requests.get(LIVE_URL, timeout=30)
    except requests.RequestException as e:
        findings.add("error", "live", f"site unreachable: {e}")
        return out
    out["index_status"] = page.status_code
    if page.status_code != 200:
        findings.add("error", "live", f"index.html answered {page.status_code}")
        return out
    for name, size in _local_build_files().items():
        # an offloaded (>95MB) file lives on a GitHub Release, referenced by full URL in index.html
        url = next((u for u in re.findall(r'"(https://[^"]+)"', page.text) if u.endswith("/" + name)),
                   LIVE_URL + "Build/" + name)
        try:
            # identity: Pages gzips .js on the fly, so the default Content-Length
            # is the compressed size and never matches the local file
            head = requests.head(url, timeout=60, allow_redirects=True,
                                 headers={"Accept-Encoding": "identity"})
        except requests.RequestException as e:
            findings.add("error", "live", f"{name}: {e}")
            continue
        remote = int(head.headers.get("Content-Length", -1))
        out["files"][name] = {"status": head.status_code, "remote_bytes": remote, "local_bytes": size}
        if head.status_code != 200:
            findings.add("error", "live", f"{name} answered {head.status_code} — the game won't load")
        elif remote not in (-1, size):
            findings.add("warning", "live", f"{name}: live {remote} bytes vs local build {size} — "
                                            f"local build not deployed yet, or CDN cache")
    return out


def check_build_freshness(findings: Findings) -> dict:
    """A WebGL build older than the last LevelDatabase/level change is
    serving an outdated campaign."""
    built_at = datetime.fromisoformat(_git(BUILD_REPO, "log", "-1", "--format=%cI").strip())
    levels_at = datetime.fromisoformat(_git(config.CANNONS_REPO, "log", "-1", "--format=%cI", "--",
                                            "Assets/Levels").strip())
    stale = levels_at > built_at
    if stale:
        findings.add("warning", "live", f"live WebGL built {built_at:%Y-%m-%d %H:%M} but levels changed "
                                        f"{levels_at:%Y-%m-%d %H:%M} — site serves the old campaign")
    return {"built_at": built_at.isoformat(), "levels_changed_at": levels_at.isoformat(), "stale": stale}


# --------------------------------------------------------------- pipeline ---

def check_pipeline(findings: Findings) -> dict:
    """2+ failures in a row of a scheduled bot = something is really broken
    (a clean budget-exhausted exit is a success, see run_learning_cycle)."""
    out = {}
    for wf in WORKFLOWS:
        result = subprocess.run(["gh", "run", "list", "--repo", "alejandroZumbado/cannonslevelgen",
                                 "--workflow", wf, "--limit", "3", "--json", "conclusion,createdAt"],
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            findings.add("warning", "pipeline", f"can't read {wf} runs ({result.stderr.strip()[:120]})")
            continue
        runs = json.loads(result.stdout)
        out[wf] = [r["conclusion"] for r in runs]
        if len(runs) >= 2 and all(r["conclusion"] == "failure" for r in runs[:2]):
            findings.add("error", "pipeline", f"{wf} failed its last 2 runs")
    return out


# --------------------------------------------------------------- forecast ---

def forecast(release_numbers: list[int], findings: Findings, days: int = 14) -> dict:
    """Daily-bot output over the last `days` days, projected to next week,
    and how many ready reserve levels exist toward the next batch."""
    today = datetime.now(timezone.utc).date()
    accepted = 0
    for d in range(days):
        path = config.AUDIT_DIR / f"{today - timedelta(days=d)}.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                e = json.loads(line)
                accepted += e.get("caller") == "daily_generator" and bool(e.get("outcome", {}).get("accepted"))
    per_week = accepted / days * 7
    in_release = set(release_numbers)
    reserve = [lv for lv in load_all(LEVELS_DIR) if lv.levelNumber not in in_release]
    ready = [lv.levelNumber for lv in reserve if pacing.pacing_report(lv).ok]
    weeks = (max(0, BATCH_SIZE - len(ready)) / per_week) if per_week else None
    if weeks is None or weeks > 8:
        findings.add("warning", "forecast", f"reserve has {len(ready)} pacing-ok levels; at "
                                            f"{per_week:.1f}/week from the daily bot the next batch of "
                                            f"{BATCH_SIZE} is {'never' if weeks is None else f'~{weeks:.0f} weeks'} "
                                            f"away — use production.batch_generator for batches")
    return {"daily_levels_last_days": accepted, "days": days, "expected_next_week": round(per_week, 1),
            "reserve_levels": len(reserve), "reserve_pacing_ok": len(ready),
            "weeks_to_next_batch_from_daily_bot": None if weeks is None else round(weeks, 1)}


# ----------------------------------------------------------------- report ---

def _markdown(report: dict) -> str:
    lines = [f"# Release gate — {report['verdict']}", "", f"_{report['generated_at']}_", ""]
    for sev, title in (("error", "BLOCK"), ("hold", "HOLD"), ("warning", "Warnings")):
        items = [i for i in report["findings"] if i["severity"] == sev]
        if items:
            lines += [f"## {title}", *[f"- [{i['section']}] {i['message']}" for i in items], ""]
    for key in ("levels", "batches", "live", "build", "pipeline", "forecast"):
        lines += [f"## {key}", "```json", json.dumps(report.get(key), indent=1, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fast", action="store_true", help="reuse the weekly audit if it is current")
    parser.add_argument("--no-live", action="store_true", help="skip network checks")
    parser.add_argument("--snapshot-deployed", action="store_true",
                        help="record the current release as deployed (run after a real deploy)")
    args = parser.parse_args(argv)

    guid_map = _guid_to_asset()
    current = release_level_numbers(DB_PATH.read_text(encoding="utf-8"), guid_map)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.snapshot_deployed:
        DEPLOYED_PATH.write_text(json.dumps({
            "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_commit": _git(config.CANNONS_REPO, "rev-parse", "HEAD").strip(),
            "levels": current}, indent=1), encoding="utf-8")
        print(f"snapshot: {len(current)} levels recorded as deployed")
        return 0

    findings = Findings()
    by_number = {lv.levelNumber: lv for lv in load_all(LEVELS_DIR)}
    release = [by_number[n] for n in current]
    deployed = (json.loads(DEPLOYED_PATH.read_text(encoding="utf-8")) if DEPLOYED_PATH.exists()
                else derive_deployed_from_git(guid_map))

    print("checking levels (replays every release level)...", flush=True)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "levels": check_levels(release, findings, args.fast),
              "batches": check_batches(current, deployed, findings),
              "build": check_build_freshness(findings),
              "live": None if args.no_live else check_live(findings),
              "pipeline": None if args.no_live else check_pipeline(findings),
              "forecast": forecast(current, findings)}
    report["verdict"] = findings.verdict()
    report["findings"] = findings.items

    (REPORT_DIR / "latest.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (REPORT_DIR / "latest.md").write_text(_markdown(report), encoding="utf-8")
    print(f"\nVERDICT: {report['verdict']}")
    for item in findings.items:
        print(f"  {item['severity'].upper():7} [{item['section']}] {item['message']}")
    return {"GO": 0, "HOLD": 2, "BLOCK": 1}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())

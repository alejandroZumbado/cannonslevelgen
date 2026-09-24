"""Weekly variety + pacing review of the shipped release (2026-09-24).

Answers "is the campaign varied and does each level stay tense?" with the
same structural checks the generators now enforce (verification/pacing.py):
  - archetype mix of the whole release and per ~11-level arc (an arc where
    most levels share one archetype plays the same trick over and over);
  - which release positions fail the pacing gate (get easier as they go,
    mostly single-pirate rounds, too long) — the candidates to rebalance.

Pure local analysis, no LLM, no simulation. Writes
reports/variety/<date>.json + latest.json; runs in weekly_level_audit.yml
right before the audit, whose git_sync commit picks the files up.

Run standalone: `python -m verification.variety_report`
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from verification.official_levels import load_all
from verification.pacing import pacing_report

ORDER_PATH = config.ROOT / "reports" / "release_order.json"
REPORT_DIR = config.ROOT / "reports" / "variety"
REPETITIVE_ARC_SHARE = 0.6  # one archetype in >= 60% of an arc's levels = repetitive arc


def build_report() -> dict:
    order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))["order"]
    by_number = {lv.levelNumber: lv for lv in load_all(config.CANNONS_REPO / "Assets" / "Levels")}
    missing = [o["levelNumber"] for o in order if o["levelNumber"] not in by_number]
    if missing:
        # explicit: a stale release_order.json vs. the real assets must not be papered over
        raise SystemExit(f"release_order.json references levels with no .asset: {missing}")

    reports = [(o, pacing_report(by_number[o["levelNumber"]])) for o in order]
    # one label per level (pacing.primary_archetype); "plain" = no clear idea
    primary_counts = Counter(r.primary or "plain" for _, r in reports)

    arcs: dict[int, list[str]] = defaultdict(list)
    for o, r in reports:
        arcs[o["arc"]].append(r.primary or "plain")
    repetitive = []
    for arc, labels in sorted(arcs.items()):
        top, n = Counter(labels).most_common(1)[0]
        if n / len(labels) >= REPETITIVE_ARC_SHARE:
            repetitive.append({"arc": arc, "archetype": top, "share": round(n / len(labels), 2)})

    failing = [{"position": o["order"], "levelNumber": o["levelNumber"], **asdict(r)}
               for o, r in reports if not r.ok]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_size": len(order),
        "pacing_pass": len(order) - len(failing),
        "primary_archetypes": dict(primary_counts),
        "repetitive_arcs": repetitive,
        "pacing_failures": failing,
    }


def main() -> None:
    report = build_report()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (REPORT_DIR / f"{stamp}.json").write_text(text, encoding="utf-8")
    (REPORT_DIR / "latest.json").write_text(text, encoding="utf-8")
    print(f"Variety: {report['pacing_pass']}/{report['release_size']} release levels pass pacing; "
          f"primary archetypes {report['primary_archetypes']}; "
          f"{len(report['repetitive_arcs'])} repetitive arc(s).")


if __name__ == "__main__":
    main()

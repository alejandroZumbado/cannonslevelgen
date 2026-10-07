"""Weekly variety + pacing review of the shipped release (2026-09-24).

Answers "is the campaign varied and does each level stay tense?" with the
same structural checks the generators now enforce (verification/pacing.py):
  - archetype mix of the whole release and per ~11-level arc (an arc where
    most levels share one archetype plays the same trick over and over);
  - which release positions fail the pacing gate (get easier as they go,
    mostly single-pirate rounds, too long) — the candidates to rebalance.

Since 2026-10-07 also the SKILL curve (verification/skill_pass.py): share of
arc bodies a naive player (policy/baseline.py, never moves a cannon) wins per
100 positions — it must fall as the campaign goes on — plus peaks the naive
player wins and near-duplicate level pairs.

Pure local analysis, no LLM (the naive player is a quick simulation). Writes
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
from policy.baseline import BaselinePolicy
from sim.engine import run_level

ORDER_PATH = config.ROOT / "reports" / "release_order.json"
REPORT_DIR = config.ROOT / "reports" / "variety"
REPETITIVE_ARC_SHARE = 0.6  # one archetype in >= 60% of an arc's levels = repetitive arc
NEAR_DUPLICATE = 0.8  # share of identical (fila, column, hp) cells, straight or mirrored


def _cells(level, mirror: bool) -> frozenset:
    return frozenset((i, 4 - c.index if mirror else c.index, c.hp)
                     for i, f in enumerate(level.filas) for c in f.cuadros if c.tipo >= 1)


def near_duplicates(levels: list) -> list[dict]:
    """Pairs of release levels that are (almost) the same layout."""
    shapes = [(lv.levelNumber, _cells(lv, False), _cells(lv, True)) for lv in levels]
    pairs = []
    for i, (a, sa, _) in enumerate(shapes):
        for b, sb, mb in shapes[i + 1:]:
            sim = max(len(sa & sb) / len(sa | sb), len(sa & mb) / len(sa | mb))
            if sim >= NEAR_DUPLICATE:
                pairs.append({"a": a, "b": b, "similarity": round(sim, 2)})
    return pairs


def skill_curve(order: list[dict], by_number: dict) -> dict:
    """Naive-player win share of arc bodies per 100 positions + naive-won peaks."""
    naive = BaselinePolicy()
    bands: dict[int, list[bool]] = defaultdict(list)
    peaks = []
    for pos, o in enumerate(order, 1):
        won = run_level(by_number[o["levelNumber"]], naive).won
        if o["role"] == "body":
            bands[(pos - 1) // 100].append(won)
        elif o["role"] == "peak" and won:
            peaks.append({"position": pos, "levelNumber": o["levelNumber"]})
    return {
        "naive_body_win_share": {f"{k * 100 + 1}-{k * 100 + 100}": round(sum(v) / len(v), 2)
                                 for k, v in sorted(bands.items())},
        "peaks_won_by_naive": peaks,
    }


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

    # role: breathers fail pacing on purpose (short, easy relief levels)
    failing = [{"position": o["order"], "levelNumber": o["levelNumber"], "role": o["role"], **asdict(r)}
               for o, r in reports if not r.ok]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_size": len(order),
        "pacing_pass": len(order) - len(failing),
        "primary_archetypes": dict(primary_counts),
        "repetitive_arcs": repetitive,
        "pacing_failures": failing,
        "skill_curve": skill_curve(order, by_number),
        "near_duplicates": near_duplicates([by_number[o["levelNumber"]] for o in order]),
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
          f"{len(report['repetitive_arcs'])} repetitive arc(s); naive body wins "
          f"{report['skill_curve']['naive_body_win_share']}; "
          f"{len(report['skill_curve']['peaks_won_by_naive'])} peak(s) won by the naive player; "
          f"{len(report['near_duplicates'])} near-duplicate pair(s).")


if __name__ == "__main__":
    main()

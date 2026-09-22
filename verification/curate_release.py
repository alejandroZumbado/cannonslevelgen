"""Curates the first closed release (200 levels) out of the manifest's
"ready" pool and orders them in Candy-Crush-style difficulty arcs (see
project memory "campaign-reorg-plan"):

  - ~11-level arcs. Each arc opens with an easy "breather" (except arc 1,
    which opens with level 1, the intro level), rises through its body, and
    ends on a peak noticeably harder than its body.
  - The floor of each arc rises a bit over the previous one.
  - Peaks of the later arcs are `solved_by_search_only` levels (the trained
    AI can't win them — real spikes); not all of them are used, only a
    spread sample, as agreed ("no tenés que meter todos").
  - Ranking uses the audit's difficulty_score as-is. No new tier system.

Deterministic: same manifest in -> same order out. Writes
`assigned_order` (1-based position) into reports/campaign_manifest.json for
every chosen level (pool -> "assigned", which campaign_manifest.py already
preserves on re-runs) and a readable reports/release_order.json.

Run standalone: `python -m verification.curate_release`
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config
from verification.official_levels import load_all

MANIFEST_PATH = config.ROOT / "reports" / "campaign_manifest.json"
OUT_PATH = config.ROOT / "reports" / "release_order.json"

RELEASE_SIZE = 200
ARC_COUNT = 18
# First arcs use champion peaks (players are still learning merges); from
# this arc on, peaks come from solved_by_search_only.
FIRST_SEARCH_PEAK_ARC = 6
INTRO_LEVEL = 1  # always position 1, it's the tutorial-ish intro level

# Percentile offsets (over champion levels sorted by difficulty) relative to
# each arc's base — see _targets_for_arc.
BODY_SPAN = 0.15
BREATHER_DROP = 0.10
PEAK_JUMP = 0.30


def _arc_sizes() -> list[int]:
    # 200 = 16 arcs of 11 + 2 arcs of 12; the longer arcs go last.
    base, extra = divmod(RELEASE_SIZE, ARC_COUNT)
    return [base + (1 if i >= ARC_COUNT - extra else 0) for i in range(ARC_COUNT)]


def _dedupe_by_shape(entries: list[dict]) -> tuple[list[dict], list[int]]:
    """Drops reskins (same fila/(index,hp) layout, see Level.shape_signature)
    so the release never ships the same challenge twice. Keeps the lowest
    levelNumber of each shape. Returns (kept, dropped levelNumbers)."""
    shapes = {lvl.levelNumber: lvl.shape_signature()
              for lvl in load_all(config.CANNONS_REPO / "Assets" / "Levels")}
    seen: set[str] = set()
    kept, dropped = [], []
    for e in sorted(entries, key=lambda e: e["levelNumber"]):
        sig = shapes.get(e["levelNumber"])
        if sig is None:
            # Manifest and assets disagree — fail loudly instead of guessing.
            raise RuntimeError(f"level {e['levelNumber']} is in the manifest but has no .asset")
        if sig in seen:
            dropped.append(e["levelNumber"])
            continue
        seen.add(sig)
        kept.append(e)
    return kept, dropped


class _Picker:
    """Picks the unused level whose rank is closest to a target percentile
    (0 = easiest, 1 = hardest) in a difficulty-sorted list."""

    def __init__(self, entries: list[dict]):
        self.sorted = sorted(entries, key=lambda e: (e["difficulty_score"], e["levelNumber"]))
        self.used: set[int] = set()

    def take(self, pct: float) -> dict:
        n = len(self.sorted)
        target = round(max(0.0, min(1.0, pct)) * (n - 1))
        # Search outward from the target rank; prefer the harder neighbour on
        # ties so peaks don't drift easier.
        for dist in range(n):
            for idx in (target + dist, target - dist):
                if 0 <= idx < n and self.sorted[idx]["levelNumber"] not in self.used:
                    self.used.add(self.sorted[idx]["levelNumber"])
                    return self.sorted[idx]
        raise RuntimeError("ran out of levels to pick")  # caller sized the pool wrong

    def take_specific(self, level_number: int) -> dict:
        for e in self.sorted:
            if e["levelNumber"] == level_number and level_number not in self.used:
                self.used.add(level_number)
                return e
        raise RuntimeError(f"level {level_number} not available in this pool")


def _targets_for_arc(k: int) -> tuple[float, float, float, float]:
    """(breather, body_start, body_end, champion_peak) percentiles for arc k.
    The base climbs from 0 to 0.85 across the release so the floor rises."""
    base = 0.85 * k / (ARC_COUNT - 1)
    return (base - BREATHER_DROP, base, base + BODY_SPAN, base + PEAK_JUMP)


def curate(manifest: dict) -> tuple[list[dict], dict]:
    ready = [e for e in manifest["levels"] if e["pool"] == "ready"]
    ready, dup_dropped = _dedupe_by_shape(ready)

    champions = [e for e in ready if e["classification"] == "champion_win"]
    searches = sorted((e for e in ready if e["classification"] == "solved_by_search_only"),
                      key=lambda e: (e["difficulty_score"], e["levelNumber"]))

    sizes = _arc_sizes()
    search_peaks_needed = ARC_COUNT - FIRST_SEARCH_PEAK_ARC
    if len(searches) < search_peaks_needed:
        raise RuntimeError(f"need {search_peaks_needed} search-only peaks, have {len(searches)}")
    if len(champions) < RELEASE_SIZE - search_peaks_needed:
        raise RuntimeError(f"not enough champion_win levels ({len(champions)}) for the release")

    # Evenly spaced sample of the search-only levels, easiest first, so the
    # later arcs get the harder spikes.
    last = len(searches) - 1
    search_peaks = [searches[round(i * last / (search_peaks_needed - 1))]
                    for i in range(search_peaks_needed)]

    picker = _Picker(champions)
    picker.take_specific(INTRO_LEVEL)  # reserve it before anything else grabs it

    # Peaks first so the hard levels go where they're meant to, then
    # breathers, then bodies.
    arcs: list[dict] = []
    for k, size in enumerate(sizes):
        breather_t, body_lo, body_hi, peak_t = _targets_for_arc(k)
        if k >= FIRST_SEARCH_PEAK_ARC:
            peak = search_peaks[k - FIRST_SEARCH_PEAK_ARC]
        else:
            peak = picker.take(peak_t)
        arcs.append({"k": k, "size": size, "peak": peak,
                     "breather_t": breather_t, "body": (body_lo, body_hi)})

    # Breathers before bodies: picked last, the easy ranks near each target
    # were already gone and breathers came out harder than their own body.
    for arc in arcs:
        if arc["k"] == 0:
            arc["opener"] = next(e for e in champions if e["levelNumber"] == INTRO_LEVEL)
            arc["opener_role"] = "intro"
        else:
            arc["opener"] = picker.take(arc["breather_t"])
            arc["opener_role"] = "breather"

    for arc in arcs:
        body_len = arc["size"] - 2  # minus opener and peak
        lo, hi = arc["body"]
        picks = [picker.take(lo + (hi - lo) * j / max(body_len - 1, 1)) for j in range(body_len)]
        # Rising within the arc, by the real score, not the target.
        picks.sort(key=lambda e: (e["difficulty_score"], e["levelNumber"]))
        # A breather must never be harder than the level after it; if the
        # picker's fallback made it so, swap it with the easiest body level.
        if arc["opener_role"] == "breather" and arc["opener"]["difficulty_score"] > picks[0]["difficulty_score"]:
            arc["opener"], picks[0] = picks[0], arc["opener"]
            picks.sort(key=lambda e: (e["difficulty_score"], e["levelNumber"]))
        arc["body_levels"] = picks

    order: list[dict] = []
    for arc in arcs:
        seq = ([(arc["opener"], arc["opener_role"])]
               + [(e, "body") for e in arc["body_levels"]]
               + [(arc["peak"], "peak")])
        for e, role in seq:
            order.append({
                "order": len(order) + 1,
                "arc": arc["k"] + 1,
                "role": role,
                "levelNumber": e["levelNumber"],
                "password": e["password"],
                "isHard": e["isHard"],
                "classification": e["classification"],
                "difficulty_score": e["difficulty_score"],
                "source": e["source"],
            })

    if len(order) != RELEASE_SIZE or len({o["levelNumber"] for o in order}) != RELEASE_SIZE:
        raise RuntimeError("curation produced a wrong-size or duplicated release")  # bug guard

    chosen = {o["levelNumber"] for o in order}
    stats = {
        "ready_before_dedupe": len(ready) + len(dup_dropped),
        "duplicate_shapes_excluded": dup_dropped,
        "reserve": sorted(e["levelNumber"] for e in ready if e["levelNumber"] not in chosen),
        "search_only_used": [p["levelNumber"] for p in search_peaks],
    }
    return order, stats


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    already = [e for e in manifest["levels"] if e["pool"] == "assigned"]
    if already:
        # Never silently reshuffle a release that was already decided.
        raise SystemExit(f"{len(already)} levels already assigned — refusing to re-curate. "
                         f"Clear assigned_order in the manifest first if that's intended.")

    order, stats = curate(manifest)

    by_number = {o["levelNumber"]: o["order"] for o in order}
    for e in manifest["levels"]:
        if e["levelNumber"] in by_number:
            e["assigned_order"] = by_number[e["levelNumber"]]
            e["pool"] = "assigned"
    counts: dict[str, int] = {}
    for e in manifest["levels"]:
        counts[e["pool"]] = counts.get(e["pool"], 0) + 1
    manifest["counts"] = counts
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    OUT_PATH.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_size": RELEASE_SIZE,
        "arc_count": ARC_COUNT,
        **stats,
        "order": order,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Release curated: {len(order)} levels in {ARC_COUNT} arcs. Manifest pools: {counts}")
    print(f"  duplicate shapes excluded: {stats['duplicate_shapes_excluded']}")
    print(f"  reserve (ready, not in release): {len(stats['reserve'])}")


if __name__ == "__main__":
    main()

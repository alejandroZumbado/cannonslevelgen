"""Curates the campaign release order out of the manifest's "ready" pool,
in Candy-Crush-style difficulty arcs (see project memory
"campaign-reorg-plan"):

  - ~11-level arcs. Each arc opens with an easy "breather" (the very first
    arc opens with level 1, the intro level), rises through its body, and
    ends on a peak noticeably harder than its body.
  - The floor of each arc rises a bit over the previous one.
  - Later peaks are `solved_by_search_only` levels (the trained AI can't
    win them — real spikes); only a spread sample is used, not all.
  - Ranking uses the audit's difficulty_score as-is. No new tier system.

Two modes, both deterministic (same manifest in -> same order out):
  - curate(): the FIRST release (200). `python -m verification.curate_release`
    Refuses to run if anything is already assigned.
  - extend(): APPENDS a new batch after the last assigned position, never
    touching existing positions (so players' saves stay valid). Used by
    verification/extend_release.py — that's the command to run for new
    batches, it also refreshes the audit and applies the result to Unity.

Both write `assigned_order` (1-based position) into
reports/campaign_manifest.json (pool -> "assigned", which
campaign_manifest.py preserves on re-runs) and the full order to
reports/release_order.json.
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
ARC_SIZE = 11  # target arc length for extension batches
# First release: early arcs use champion peaks (players still learning
# merges); from this arc on, peaks come from solved_by_search_only.
FIRST_SEARCH_PEAK_ARC = 6
INTRO_LEVEL = 1  # always position 1, it's the tutorial-ish intro level
MIN_ARC_SIZE = 3  # breather + body + peak; smaller batches are just appended in order

# Percentile offsets (over champion levels sorted by difficulty) relative to
# each arc's base — see _targets_for_arc.
BODY_SPAN = 0.15
BREATHER_DROP = 0.10
PEAK_JUMP = 0.30


def _arc_sizes(total: int, arc_count: int) -> list[int]:
    # e.g. 200/18 = 16 arcs of 11 + 2 of 12; the longer arcs go last.
    base, extra = divmod(total, arc_count)
    return [base + (1 if i >= arc_count - extra else 0) for i in range(arc_count)]


def _shapes_by_level_number() -> dict[int, str]:
    return {lvl.levelNumber: lvl.shape_signature()
            for lvl in load_all(config.CANNONS_REPO / "Assets" / "Levels")}


def _dedupe_by_shape(entries: list[dict], taken_shapes: set[str] | None = None
                     ) -> tuple[list[dict], list[int]]:
    """Drops reskins (same fila/(index,hp) layout, see Level.shape_signature)
    so the release never ships the same challenge twice — within `entries`
    and against `taken_shapes` (levels already in the release). Keeps the
    lowest levelNumber of each shape. Returns (kept, dropped levelNumbers)."""
    shapes = _shapes_by_level_number()
    seen: set[str] = set(taken_shapes or ())
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


def _targets_for_arc(k: int, arc_count: int) -> tuple[float, float, float, float]:
    """(breather, body_start, body_end, champion_peak) percentiles for arc k.
    The base climbs from 0 to 0.85 across the batch so the floor rises."""
    base = 0.85 * k / max(arc_count - 1, 1)
    return (base - BREATHER_DROP, base, base + BODY_SPAN, base + PEAK_JUMP)


def _sample_evenly(items: list[dict], n: int) -> list[dict]:
    # Evenly spaced picks, easiest first, so later arcs get harder spikes.
    if n <= 0:
        return []
    if n == 1:
        return [items[0]]
    last = len(items) - 1
    return [items[round(i * last / (n - 1))] for i in range(n)]


def _entry(e: dict, role: str, arc: int) -> dict:
    return {
        "arc": arc,
        "role": role,
        "levelNumber": e["levelNumber"],
        "password": e["password"],
        "isHard": e["isHard"],
        "classification": e["classification"],
        "difficulty_score": e["difficulty_score"],
        "source": e["source"],
    }


def _build_arcs(champions: list[dict], search_peaks: list[dict], sizes: list[int],
                first_search_peak_arc: int, intro_level: int | None, arc_offset: int) -> list[dict]:
    """Core arc builder shared by curate() and extend(). Arcs k >=
    first_search_peak_arc take search_peaks in order as their peak; the rest
    get a champion peak. Returns order entries (without the `order` number)."""
    arc_count = len(sizes)
    picker = _Picker(champions)
    if intro_level is not None:
        picker.take_specific(intro_level)  # reserve it before anything else grabs it

    # Peaks first so the hard levels go where they're meant to, then
    # breathers, then bodies.
    arcs: list[dict] = []
    for k, size in enumerate(sizes):
        breather_t, body_lo, body_hi, peak_t = _targets_for_arc(k, arc_count)
        if k >= first_search_peak_arc:
            peak = search_peaks[k - first_search_peak_arc]
        else:
            peak = picker.take(peak_t)
        arcs.append({"k": k, "size": size, "peak": peak,
                     "breather_t": breather_t, "body": (body_lo, body_hi)})

    # Breathers before bodies: picked last, the easy ranks near each target
    # were already gone and breathers came out harder than their own body.
    for arc in arcs:
        if arc["k"] == 0 and intro_level is not None:
            arc["opener"] = next(e for e in champions if e["levelNumber"] == intro_level)
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
        if picks and arc["opener_role"] == "breather" and \
                arc["opener"]["difficulty_score"] > picks[0]["difficulty_score"]:
            arc["opener"], picks[0] = picks[0], arc["opener"]
            picks.sort(key=lambda e: (e["difficulty_score"], e["levelNumber"]))
        arc["body_levels"] = picks

    order: list[dict] = []
    for arc in arcs:
        n = arc["k"] + 1 + arc_offset
        order.append(_entry(arc["opener"], arc["opener_role"], n))
        order += [_entry(e, "body", n) for e in arc["body_levels"]]
        order.append(_entry(arc["peak"], "peak", n))
    return order


def _split_pool(ready: list[dict]) -> tuple[list[dict], list[dict]]:
    champions = [e for e in ready if e["classification"] == "champion_win"]
    searches = sorted((e for e in ready if e["classification"] == "solved_by_search_only"),
                      key=lambda e: (e["difficulty_score"], e["levelNumber"]))
    return champions, searches


def curate(manifest: dict) -> tuple[list[dict], dict]:
    """First release: RELEASE_SIZE levels in ARC_COUNT arcs, intro first."""
    ready = [e for e in manifest["levels"] if e["pool"] == "ready"]
    ready, dup_dropped = _dedupe_by_shape(ready)
    champions, searches = _split_pool(ready)

    search_peaks_needed = ARC_COUNT - FIRST_SEARCH_PEAK_ARC
    if len(searches) < search_peaks_needed:
        raise RuntimeError(f"need {search_peaks_needed} search-only peaks, have {len(searches)}")
    if len(champions) < RELEASE_SIZE - search_peaks_needed:
        raise RuntimeError(f"not enough champion_win levels ({len(champions)}) for the release")

    search_peaks = _sample_evenly(searches, search_peaks_needed)
    order = _build_arcs(champions, search_peaks, _arc_sizes(RELEASE_SIZE, ARC_COUNT),
                        FIRST_SEARCH_PEAK_ARC, INTRO_LEVEL, arc_offset=0)
    for i, o in enumerate(order):
        o["order"] = i + 1
    _check_unique(order, RELEASE_SIZE)

    chosen = {o["levelNumber"] for o in order}
    stats = {
        "duplicate_shapes_excluded": dup_dropped,
        "search_only_used": [p["levelNumber"] for p in search_peaks],
        "reserve": sorted(e["levelNumber"] for e in ready if e["levelNumber"] not in chosen),
    }
    return order, stats


def _batch_capacity(n_champions: int, n_searches: int, wanted: int) -> int:
    """Largest batch <= wanted that the pool can fill under the rule
    "search-only levels only as peaks, one per arc"."""
    for size in range(wanted, 0, -1):
        if size < MIN_ARC_SIZE:
            if n_champions >= size:
                return size
            continue
        arc_count = max(1, round(size / ARC_SIZE))
        if n_champions + min(arc_count, n_searches) >= size:
            return size
    return 0


class NotEnoughLevels(RuntimeError):
    """The ready pool can't fill the requested batch — explicit, never a
    silently smaller batch unless the caller allowed it."""


def extend(manifest: dict, existing_order: list[dict], count: int, allow_fewer: bool = False
           ) -> tuple[list[dict], dict]:
    """Appends `count` levels after the last assigned position. Existing
    positions are never touched. Search-only levels are used as the peaks
    of the LAST arcs of the batch (as many as available, one per arc)."""
    if count < 1:
        raise ValueError("count must be >= 1")
    assigned = {o["levelNumber"] for o in existing_order}
    shapes = _shapes_by_level_number()
    taken_shapes = {shapes[n] for n in assigned if n in shapes}

    ready = [e for e in manifest["levels"] if e["pool"] == "ready" and e["levelNumber"] not in assigned]
    ready, dup_dropped = _dedupe_by_shape(ready, taken_shapes)
    champions, searches = _split_pool(ready)

    last_order = max((o["order"] for o in existing_order), default=0)
    last_arc = max((o["arc"] for o in existing_order), default=0)

    # How many levels the pool can really give: search-only levels are only
    # used as arc peaks (one per arc), everything else must be champion_win.
    capacity = _batch_capacity(len(champions), len(searches), count)
    if capacity < count:
        if not allow_fewer or capacity == 0:
            raise NotEnoughLevels(
                f"asked for {count} new levels but the ready pool can only fill {capacity} "
                f"({len(champions)} champion_win + {len(searches)} solved_by_search_only usable "
                f"only as arc peaks, {len(dup_dropped)} excluded as reskins). Add more levels "
                f"first, or pass --allow-fewer to add {capacity}.")
        count = capacity

    if count < MIN_ARC_SIZE:
        # Too few for an arc: append easiest-first, champion levels only.
        picked = sorted(champions, key=lambda e: (e["difficulty_score"], e["levelNumber"]))[:count]
        new = [_entry(e, "body", last_arc + 1) for e in picked]
        search_used: list[int] = []
    else:
        arc_count = max(1, round(count / ARC_SIZE))
        n_search = min(arc_count, len(searches))
        search_peaks = _sample_evenly(searches, n_search)
        new = _build_arcs(champions, search_peaks, _arc_sizes(count, arc_count),
                          first_search_peak_arc=arc_count - n_search,
                          intro_level=None, arc_offset=last_arc)
        search_used = [p["levelNumber"] for p in search_peaks]

    for i, o in enumerate(new):
        o["order"] = last_order + i + 1
    _check_unique(existing_order + new, len(existing_order) + len(new))

    chosen = assigned | {o["levelNumber"] for o in new}
    stats = {
        "duplicate_shapes_excluded": dup_dropped,
        "search_only_used": search_used,
        "reserve": sorted(e["levelNumber"] for e in ready if e["levelNumber"] not in chosen),
    }
    return new, stats


def _check_unique(order: list[dict], expected: int) -> None:
    # bug guard: every position filled once, no level twice
    if len(order) != expected or len({o["levelNumber"] for o in order}) != expected:
        raise RuntimeError("curation produced a wrong-size or duplicated release")
    if [o["order"] for o in order] != list(range(1, expected + 1)):
        raise RuntimeError("release positions are not a contiguous 1..N sequence")


def load_existing_order() -> list[dict]:
    if not OUT_PATH.exists():
        return []
    return json.loads(OUT_PATH.read_text(encoding="utf-8"))["order"]


def save(manifest: dict, full_order: list[dict], stats: dict, batch_size: int) -> dict:
    """Writes assigned_order into the manifest and the full release order.
    Returns the per-pool counts."""
    by_number = {o["levelNumber"]: o["order"] for o in full_order}
    for e in manifest["levels"]:
        if e["levelNumber"] in by_number:
            e["assigned_order"] = by_number[e["levelNumber"]]
            e["pool"] = "assigned"
    counts: dict[str, int] = {}
    for e in manifest["levels"]:
        counts[e["pool"]] = counts.get(e["pool"], 0) + 1
    manifest["counts"] = counts
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    previous = json.loads(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    batches = previous.get("batches") or [{"added_at": previous.get("generated_at", now),
                                           "first": 1, "last": len(previous.get("order", []))}] if previous else []
    batches = batches + [{"added_at": now, "first": len(full_order) - batch_size + 1,
                          "last": len(full_order)}]
    OUT_PATH.write_text(json.dumps({
        "generated_at": now,
        "release_size": len(full_order),
        "batches": batches,
        **stats,
        "order": full_order,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return counts


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    already = [e for e in manifest["levels"] if e["pool"] == "assigned"]
    if already:
        # Never silently reshuffle a release that was already decided.
        raise SystemExit(f"{len(already)} levels already assigned — refusing to re-curate. "
                         f"To add levels use: python -m verification.extend_release --count N")

    order, stats = curate(manifest)
    if OUT_PATH.exists():
        OUT_PATH.unlink()  # fresh first release: no previous batches
    counts = save(manifest, order, stats, batch_size=len(order))

    print(f"Release curated: {len(order)} levels in {ARC_COUNT} arcs. Manifest pools: {counts}")
    print(f"  duplicate shapes excluded: {stats['duplicate_shapes_excluded']}")
    print(f"  reserve (ready, not in release): {len(stats['reserve'])}")


if __name__ == "__main__":
    main()

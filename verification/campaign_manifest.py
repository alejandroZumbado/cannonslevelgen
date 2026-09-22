"""Campaign manifest: categorizes every real level into exactly one pool,
so the upcoming curation of the first 200-level release (see project memory
"campaign-reorg-plan") has a clear, reusable picture of what's available —
instead of re-deriving it from raw audit/repair/rebalance/fill reports each
time.

Pools (a level is in exactly one):
  - "assigned"        — already given a fixed slot/order in a release.
                         Empty until the 200-level cut is actually decided;
                         this script never assigns levels itself, only a
                         human curation pass (or a future script) does, by
                         writing to this field and re-running with
                         --preserve-assigned (see main()).
  - "ready"           — winnable (champion_win or solved_by_search_only),
                         no known issues. The pool the 200-cut draws from.
  - "problema_vacio"  — winnable, but has at least one dead round
                         (fila with zero pirates) that verification/
                         fill_empty_rounds.py could NOT fill without making
                         the level worse — cosmetic imperfection, not
                         unwinnable, kept separate from "roto" on purpose
                         since it needs a different kind of attention (a
                         manual look at that one fila, not a rewrite).
  - "roto"            — no_win_found: neither the champion nor a wide
                         search win it. Best evidence available that the
                         level is broken, not a certified proof.

Reads reports/level_audit/latest.json (real classification, must be current
— re-run verification/level_audit.py first if levels changed since) and
reports/fill_empty_rounds/results.json (for the "problema_vacio" signal).
Writes reports/campaign_manifest.json.

Run standalone: `python -m verification.campaign_manifest`
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import config

AUDIT_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"
FILL_PATH = config.ROOT / "reports" / "fill_empty_rounds" / "results.json"
OUT_PATH = config.ROOT / "reports" / "campaign_manifest.json"

WINNABLE = {"champion_win", "solved_by_search_only"}


@dataclass
class ManifestEntry:
    levelNumber: int
    password: str
    isHard: bool
    classification: str
    difficulty_score: float
    pool: str
    assigned_order: int | None = None  # position in the release, once curated
    source: str = "original"  # "original" | "ai_generated"


def _load_previous_assignments() -> dict[int, int]:
    """levelNumber -> assigned_order for any level a previous manifest run
    already curated into the "assigned" pool — preserved across re-runs so
    re-generating the manifest after new levels arrive never un-assigns
    ones already locked into the release order."""
    if not OUT_PATH.exists():
        return {}
    old = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    return {
        e["levelNumber"]: e["assigned_order"]
        for e in old.get("levels", [])
        if e.get("pool") == "assigned" and e.get("assigned_order") is not None
    }


def _levels_with_empty_rounds() -> set[int] | None:
    """levelNumbers whose CURRENT asset has a fila with zero pirates, read
    straight from Cannons' Assets/Levels. The fill-results report only knows
    the original 500 levels, so a newly added level with a dead round would
    otherwise be classified "ready". None if the Cannons checkout isn't
    available (e.g. CI without it) — caller falls back to the report."""
    levels_dir = config.CANNONS_REPO / "Assets" / "Levels"
    if not levels_dir.is_dir():
        return None
    from verification.official_levels import load_all
    return {
        lvl.levelNumber for lvl in load_all(levels_dir)
        if any(not any(c.tipo >= 1 for c in fila.cuadros) for fila in lvl.filas)
    }


def build_manifest(extra_levels: list[dict] | None = None) -> dict:
    """extra_levels: optional list of {levelNumber, password, isHard,
    classification, difficulty_score} dicts for levels not yet in the
    official audit (e.g. freshly imported AI-generated ones) — merged in
    as source="ai_generated"."""
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    fill = json.loads(FILL_PATH.read_text(encoding="utf-8")) if FILL_PATH.exists() else {}
    previous_assignments = _load_previous_assignments()
    empty_from_assets = _levels_with_empty_rounds()

    rows = [dict(lvl, source="original") for lvl in audit["levels"]]
    if extra_levels:
        rows += [dict(lvl, source="ai_generated") for lvl in extra_levels]

    entries: list[ManifestEntry] = []
    for lvl in rows:
        n = lvl["levelNumber"]
        classification = lvl["classification"]
        if empty_from_assets is not None:
            has_unfixed_empty = n in empty_from_assets
        else:
            has_unfixed_empty = bool(fill.get(str(n), {}).get("filas_left_empty"))

        if n in previous_assignments:
            pool = "assigned"
        elif classification == "no_win_found":
            pool = "roto"
        elif has_unfixed_empty:
            pool = "problema_vacio"
        elif classification in WINNABLE:
            pool = "ready"
        else:
            pool = "roto"  # defensive fallback, should never hit given the 3 known classifications

        entries.append(ManifestEntry(
            levelNumber=n,
            password=lvl["password"],
            isHard=bool(lvl["isHard"]),
            classification=classification,
            difficulty_score=lvl["difficulty_score"],
            pool=pool,
            assigned_order=previous_assignments.get(n),
            source=lvl.get("source", "original"),
        ))

    entries.sort(key=lambda e: e.levelNumber)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.pool] = counts.get(e.pool, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "based_on_audit": audit["generated_at"],
        "total_levels": len(entries),
        "counts": counts,
        "levels": [asdict(e) for e in entries],
    }


def main() -> None:
    manifest = build_manifest()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Manifest written: {manifest['total_levels']} levels, {manifest['counts']}")


if __name__ == "__main__":
    main()

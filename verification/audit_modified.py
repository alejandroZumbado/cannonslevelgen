"""One-off comparison tool: re-audits the 500 real levels with the pending
edit sets from reports/repair/results.json (35 HP-down fixes) and
reports/rebalance/results.json (96 HP-up edits across 74 levels) applied
IN MEMORY, so the impact of both can be seen before deciding whether to
apply them to the real .asset files.

Deliberately writes to its own output file, never touches
reports/level_audit/latest.json — that file must keep reflecting the real,
currently-shipped game for repair_impossible.py/rebalance_filler.py's own
"what's still broken/what's still winnable" bookkeeping to stay correct.

Run standalone: `python -m verification.audit_modified`
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

import config
from policy.loader import load_policy_from_file
from verification.level_audit import audit_level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
OUT_PATH = config.ROOT / "reports" / "level_audit" / "simulated_with_pending_edits.json"
REPAIR_PATH = config.ROOT / "reports" / "repair" / "results.json"
REBALANCE_PATH = config.ROOT / "reports" / "rebalance" / "results.json"
BASELINE_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"


def _apply_pending_edits(levels_by_number: dict) -> tuple[dict, int, int]:
    repair = json.loads(REPAIR_PATH.read_text(encoding="utf-8"))
    rebalance = json.loads(REBALANCE_PATH.read_text(encoding="utf-8"))

    repair_applied = 0
    for num_str, r in repair.items():
        if r.get("fixed"):
            lvl = levels_by_number[int(num_str)]
            lvl.filas[r["fila_index"]].cuadros[r["cuadro_index"]].hp = r["new_hp"]
            repair_applied += 1

    rebalance_applied = 0
    for num_str, r in rebalance.items():
        if r.get("reworked"):
            lvl = levels_by_number[int(num_str)]
            for e in r["edits"]:
                lvl.filas[e["fila_index"]].cuadros[e["cuadro_index"]].hp = e["new_hp"]
            rebalance_applied += 1

    return levels_by_number, repair_applied, rebalance_applied


def main() -> None:
    levels_by_number = {lv.levelNumber: copy.deepcopy(lv) for lv in load_all(CANNONS_LEVELS_DIR)}
    levels_by_number, n_repair, n_rebalance = _apply_pending_edits(levels_by_number)
    champion = load_policy_from_file(config.ROOT / "policy" / "current.py")

    t0 = time.time()
    reports = []
    for num in sorted(levels_by_number):
        report = audit_level(levels_by_number[num], champion, ESCALATION_BEAM_WIDTHS, DEFAULT_MAX_ROUNDS)
        reports.append(report)
        print(f"level {num}: {report.classification}")
    elapsed = time.time() - t0

    counts = {"champion_win": 0, "solved_by_search_only": 0, "no_win_found": 0}
    for r in reports:
        counts[r.classification] += 1

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else None
    baseline_by_num = {lvl["levelNumber"]: lvl["classification"] for lvl in baseline["levels"]} if baseline else {}

    changed = []
    for r in reports:
        prev = baseline_by_num.get(r.levelNumber)
        if prev is not None and prev != r.classification:
            changed.append({"levelNumber": r.levelNumber, "password": r.password,
                             "from": prev, "to": r.classification})

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "SIMULATED — repair (fixed levels) + rebalance (reworked levels) edits "
                "applied IN MEMORY only. NOT the real shipped game. "
                "reports/level_audit/latest.json is untouched.",
        "policy_name": getattr(champion, "name", "unknown"),
        "total_levels": len(levels_by_number),
        "repair_edits_applied": n_repair,
        "rebalance_edits_applied": n_rebalance,
        "elapsed_seconds": round(elapsed, 1),
        "counts": counts,
        "baseline_counts": baseline["counts"] if baseline else None,
        "baseline_generated_at": baseline["generated_at"] if baseline else None,
        "changed_from_baseline": sorted(changed, key=lambda c: c["levelNumber"]),
        "levels": [asdict(r) for r in sorted(reports, key=lambda r: r.difficulty_score, reverse=True)],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\ndone in {elapsed:.1f}s: {counts}")
    print(f"vs baseline {baseline['counts'] if baseline else 'N/A'}: {len(changed)} levels changed classification")


if __name__ == "__main__":
    main()

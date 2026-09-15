"""Same purpose as audit_modified.py but with all THREE pending edit sets
applied together: reports/repair/results.json (HP down, fixes no_win_found
levels), reports/rebalance/results.json (HP up, fixes trivial filler
pirates), and reports/fill_empty_rounds/results.json (fills dead rounds).
The three never touch the same (fila_index, cuadro_index) — repair/
rebalance only edit cuadros that already existed in a non-empty fila,
fill_empty_rounds only ever turns a previously-EMPTY fila into a 1-cuadro
fila — so applying all three to the same in-memory copy is safe.

Writes its own dated report, never touches reports/level_audit/latest.json
(the real-game reference) or the individual tools' own results.json files.

Run standalone: `python -m verification.audit_all_pending`
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

import config
from policy.loader import load_policy_from_file
from sim.level import Cuadro, Fila
from verification.level_audit import audit_level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
OUT_PATH = config.ROOT / "reports" / "level_audit" / "simulated_with_all_pending_edits.json"
REPAIR_PATH = config.ROOT / "reports" / "repair" / "results.json"
REBALANCE_PATH = config.ROOT / "reports" / "rebalance" / "results.json"
FILL_PATH = config.ROOT / "reports" / "fill_empty_rounds" / "results.json"
BASELINE_PATH = config.ROOT / "reports" / "level_audit" / "latest.json"


def _apply_all(levels_by_number: dict) -> dict:
    repair = json.loads(REPAIR_PATH.read_text(encoding="utf-8"))
    rebalance = json.loads(REBALANCE_PATH.read_text(encoding="utf-8"))
    fill = json.loads(FILL_PATH.read_text(encoding="utf-8"))
    counts = {"repair": 0, "rebalance": 0, "fill": 0}

    for num_str, r in repair.items():
        if r.get("fixed"):
            lvl = levels_by_number[int(num_str)]
            lvl.filas[r["fila_index"]].cuadros[r["cuadro_index"]].hp = r["new_hp"]
            counts["repair"] += 1

    for num_str, r in rebalance.items():
        if r.get("reworked"):
            lvl = levels_by_number[int(num_str)]
            for e in r["edits"]:
                lvl.filas[e["fila_index"]].cuadros[e["cuadro_index"]].hp = e["new_hp"]
            counts["rebalance"] += 1

    for num_str, r in fill.items():
        if r.get("filas_fixed"):
            lvl = levels_by_number[int(num_str)]
            for e in r["filas_fixed"]:
                lvl.filas[e["fila_index"]] = Fila(cuadros=[Cuadro(index=e["column"], tipo=1, hp=1)])
            counts["fill"] += 1

    return counts


def main() -> None:
    levels_by_number = {lv.levelNumber: copy.deepcopy(lv) for lv in load_all(CANNONS_LEVELS_DIR)}
    counts = _apply_all(levels_by_number)
    champion = load_policy_from_file(config.ROOT / "policy" / "current.py")

    t0 = time.time()
    reports = []
    for num in sorted(levels_by_number):
        report = audit_level(levels_by_number[num], champion, ESCALATION_BEAM_WIDTHS, DEFAULT_MAX_ROUNDS)
        reports.append(report)
        print(f"level {num}: {report.classification}")
    elapsed = time.time() - t0

    class_counts = {"champion_win": 0, "solved_by_search_only": 0, "no_win_found": 0}
    for r in reports:
        class_counts[r.classification] += 1

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
        "note": "SIMULATED — repair + rebalance + fill_empty_rounds edits applied IN MEMORY together. "
                "NOT the real shipped game. reports/level_audit/latest.json is untouched.",
        "policy_name": getattr(champion, "name", "unknown"),
        "total_levels": len(levels_by_number),
        "levels_touched_by_tool": counts,
        "elapsed_seconds": round(elapsed, 1),
        "counts": class_counts,
        "baseline_counts": baseline["counts"] if baseline else None,
        "baseline_generated_at": baseline["generated_at"] if baseline else None,
        "changed_from_baseline": sorted(changed, key=lambda c: c["levelNumber"]),
        "levels": [asdict(r) for r in sorted(reports, key=lambda r: r.difficulty_score, reverse=True)],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\ndone in {elapsed:.1f}s: {class_counts}")
    print(f"levels touched per tool: {counts}")
    print(f"vs baseline {baseline['counts'] if baseline else 'N/A'}: {len(changed)} levels changed classification")


if __name__ == "__main__":
    main()

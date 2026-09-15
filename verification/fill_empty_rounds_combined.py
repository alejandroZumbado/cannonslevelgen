"""Corrected fill pass: verification/fill_empty_rounds.py verified each dead-
round fix against the PRISTINE real level — correct in isolation, but 5
levels regressed once combined with repair_impossible.py's and
rebalance_filler.py's edits on top (confirmed 2026-09-15: e.g. level 272 —
fill's fila-1 pirate was safe against the original no_win_found level, but
breaks once repair's separate HP-down fix at fila 0 is ALSO applied; level
199 — fill's edit was safe against the original champion_win level, but
breaks once rebalance's filler-HP-up edits are ALSO applied). The three
tools' safety checks aren't composable when run independently against the
pristine baseline — each has to see what the OTHER accepted edits already
did to the same level.

This is fill_empty_rounds.py's exact `fix_level()` logic, unchanged, but fed
levels that already have the repair + rebalance edits applied, and baselined
against THAT combined state's classification (from
reports/level_audit/simulated_with_pending_edits.json) instead of the real
game's. Supersedes reports/fill_empty_rounds/results.json for any level that
repair or rebalance also touched; identical result for every level neither
of them touched (the vast majority — verified below).

Run standalone: `python -m verification.fill_empty_rounds_combined`
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict

import config
import git_sync
from policy.loader import load_policy_from_file
from sim.level import Cuadro, Fila
from verification.fill_empty_rounds import fix_level
from verification.official_levels import load_all
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

CANNONS_LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"
REPAIR_PATH = config.ROOT / "reports" / "repair" / "results.json"
REBALANCE_PATH = config.ROOT / "reports" / "rebalance" / "results.json"
TWO_TOOL_AUDIT_PATH = config.ROOT / "reports" / "level_audit" / "simulated_with_pending_edits.json"
OUT_PATH = config.ROOT / "reports" / "fill_empty_rounds" / "results.json"
OLD_RESULTS_PATH = config.ROOT / "reports" / "fill_empty_rounds" / "results_standalone_vs_real_game.json"


def _apply_repair_and_rebalance(levels_by_number: dict) -> None:
    repair = json.loads(REPAIR_PATH.read_text(encoding="utf-8"))
    rebalance = json.loads(REBALANCE_PATH.read_text(encoding="utf-8"))
    for num_str, r in repair.items():
        if r.get("fixed"):
            lvl = levels_by_number[int(num_str)]
            lvl.filas[r["fila_index"]].cuadros[r["cuadro_index"]].hp = r["new_hp"]
    for num_str, r in rebalance.items():
        if r.get("reworked"):
            lvl = levels_by_number[int(num_str)]
            for e in r["edits"]:
                lvl.filas[e["fila_index"]].cuadros[e["cuadro_index"]].hp = e["new_hp"]


def main() -> None:
    import copy
    levels_by_number = {lv.levelNumber: copy.deepcopy(lv) for lv in load_all(CANNONS_LEVELS_DIR)}
    _apply_repair_and_rebalance(levels_by_number)

    two_tool_audit = json.loads(TWO_TOOL_AUDIT_PATH.read_text(encoding="utf-8"))
    classifications = {lvl["levelNumber"]: lvl["classification"] for lvl in two_tool_audit["levels"]}

    champion = load_policy_from_file(config.ROOT / "policy" / "current.py")
    widths = tuple(two_tool_audit.get("escalation_beam_widths", ESCALATION_BEAM_WIDTHS))

    # Preserve the original (pristine-baseline) results for reference before overwriting.
    if OUT_PATH.exists() and not OLD_RESULTS_PATH.exists():
        OLD_RESULTS_PATH.write_text(OUT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    t0 = time.time()
    results = {}
    changed_vs_v1 = []
    old_results = json.loads(OLD_RESULTS_PATH.read_text(encoding="utf-8")) if OLD_RESULTS_PATH.exists() else {}
    for number in sorted(levels_by_number):
        level = levels_by_number[number]
        result = fix_level(level, champion, widths, DEFAULT_MAX_ROUNDS, classifications[number])
        results[number] = asdict(result)
        old = old_results.get(str(number))
        if old and old.get("filas_fixed") != result.filas_fixed:
            changed_vs_v1.append(number)
    elapsed = time.time() - t0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({str(k): v for k, v in sorted(results.items())}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    touched = sum(1 for r in results.values() if r["filas_fixed"])
    print(f"done in {elapsed:.1f}s: {touched}/{len(results)} levels had dead rounds fixed "
          f"(re-verified against the repair+rebalance-combined state)")
    print(f"placements that differ from the standalone-vs-real-game pass: {len(changed_vs_v1)} levels: {changed_vs_v1}")

    git_sync.commit_and_push(
        f"[bot] recompute fill_empty_rounds against repair+rebalance-combined state ({touched}/{len(results)} fixed)"
    )


if __name__ == "__main__":
    main()

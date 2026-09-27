"""Fault-injection tests for verification/release_gate.py: each test plants
one thing that must never reach players and checks the gate catches it.
Run with: python tests/test_release_gate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim.level import Level, Fila, Cuadro
from verification import release_gate as gate


def lvl(number, password, filas_spec):
    """filas_spec: list of rounds, each a list of (column, hp)."""
    filas = [Fila(cuadros=[Cuadro(index=c, tipo=1, hp=h) for (c, h) in fila]) for fila in filas_spec]
    return Level(levelNumber=number, password=password, isHard=False, filas=filas)


EASY = [[(2, 1)], [(1, 1), (3, 1)], [(0, 1), (4, 1)]]
IMPOSSIBLE = [[(c, 10) for c in range(5)]]  # 5 HP-10 pirates at once: no merge can be ready in time


def _levels_findings(release):
    findings = gate.Findings()
    gate.check_levels(release, findings, fast=False)
    return findings


def test_clean_release_has_no_errors():
    findings = _levels_findings([lvl(1, "A0001", EASY), lvl(2, "B0002", EASY)])
    assert not [i for i in findings.items if i["severity"] == "error"], findings.items


def test_unwinnable_level_blocks():
    findings = _levels_findings([lvl(1, "A0001", EASY), lvl(2, "B0002", IMPOSSIBLE)])
    assert findings.verdict() == "BLOCK"
    assert any("position 2" in i["message"] and "no_win_found" in i["message"] for i in findings.items)


def test_duplicate_password_blocks():
    findings = _levels_findings([lvl(1, "A0001", EASY), lvl(2, "a0001 ", EASY)])  # trim+upper collide
    assert any("password A0001" in i["message"] for i in findings.items), findings.items


def test_out_of_range_hp_blocks():
    findings = _levels_findings([lvl(1, "A0001", [[(2, 12)]])])
    assert findings.verdict() == "BLOCK"


def test_empty_round_blocks():
    findings = _levels_findings([lvl(1, "A0001", [[(2, 1)], [], [(1, 1)]])])
    assert any("no pirates" in i["message"] for i in findings.items)


def test_batch_of_100_goes():
    findings = gate.Findings()
    gate.check_batches(list(range(1, 301)), {"levels": list(range(1, 201))}, findings)
    assert findings.verdict() == "GO", findings.items


def test_partial_batch_holds():
    findings = gate.Findings()
    stats = gate.check_batches(list(range(1, 251)), {"levels": list(range(1, 201))}, findings)
    assert findings.verdict() == "HOLD" and stats["new"] == 50


def test_nothing_new_holds():
    findings = gate.Findings()
    gate.check_batches(list(range(1, 201)), {"levels": list(range(1, 201))}, findings)
    assert findings.verdict() == "HOLD"


def test_moved_position_blocks_once_published():
    current = [2, 1] + list(range(3, 103))
    deployed = {"levels": [1, 2]}
    saved = gate.GAME_PUBLISHED
    try:
        gate.GAME_PUBLISHED = False
        before = gate.Findings()
        gate.check_batches(current, deployed, before)
        gate.GAME_PUBLISHED = True
        after = gate.Findings()
        gate.check_batches(current, deployed, after)
    finally:
        gate.GAME_PUBLISHED = saved
    assert before.verdict() == "GO" and after.verdict() == "BLOCK", (before.items, after.items)


def test_shrinking_release_blocks():
    findings = gate.Findings()
    gate.check_batches(list(range(1, 101)), {"levels": list(range(1, 201))}, findings)
    assert findings.verdict() == "BLOCK"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)

"""Pacing + variety checks for GENERATED levels — pure structure, no sim, no LLM.

Why (2026-09-24): winnability was the only gate, so the daily LLM shipped
Level 508 — 27 rounds, one pirate each, HP creeping 2 -> 12. It *looks* like
it ramps, but the player gains +1 cannon every round: by round 25 they own
25 cannons vs. one HP-10 pirate. Difficulty has to be measured against the
firepower the player has accumulated, not in absolute HP.

Demand model (from the real rules, learning/game_rules.py):
  - a pirate gets exactly 3 shots, so killing hp `h` needs a column of damage
    ceil(h/3) while it's alive;
  - pirates from the last 3 filas are alive together;
  - in round r (1-based) the player has placed at most r cannons.
  demand_ratio(r) = sum(ceil(hp/3) over filas r-2..r) / r
A ratio that collapses late = the level gets easier as it goes (193/200
levels of the first release do this; its late-third median is 0.83, p10 0.55;
Level 508 = 0.39).

Width over height: the same threat spread over several pirates/columns is
better design than one tall pirate (2 x HP7 instead of 1 x HP14) — it makes
the player split cannons across columns instead of stacking one. Hence the
single-pirate-fila cap below.

Thresholds are for NEW levels and deliberately sit around the release median,
so new content is at least as tense as what already ships.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field

from sim.level import Level

MAX_FILAS = 12                 # release p90 is 9; 508 had 27
MAX_SINGLE_PIRATE_SHARE = 0.5  # release median; bot levels 502-509 were 0.78-1.0
MIN_LATE_DEMAND = 0.7          # release p25 0.67 / median 0.83

# Design archetypes — the "strategies" a level can be built around. Detected
# from structure, so the generators can aim for under-represented ones.
ARCHETYPES: dict[str, str] = {
    "swarm": "many low-HP pirates (HP 1-3) across 3-5 columns per round — tests spreading cannons wide",
    "tank": "a few high-HP pirates (HP 5-10) that need merges prepared in advance in their column",
    "wall": "pirates stacked in the SAME column on consecutive rounds, so the front one blocks shots "
            "meant for the ones behind — tests concentrating damage",
    "crescendo": "starts light and ends with the heaviest rounds (most pirates and HP in the last third)",
    "burst": "calm rounds (1-2 pirates) then sudden rounds of 4-5 pirates at once — tests keeping reserves",
    "switch": "pressure concentrates on one side (columns 0-1) then jumps to the other (3-4) — "
              "tests MOVING cannons, not only placing new ones",
}


@dataclass
class PacingReport:
    filas: int
    pirates_per_fila: float
    single_pirate_share: float
    late_demand: float
    archetypes: list[str] = field(default_factory=list)
    primary: str | None = None  # the one archetype this level is "about"
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _pirates(fila) -> list:
    return [c for c in fila.cuadros if c.tipo >= 1]


def demand_ratios(level: Level) -> list[float]:
    """demand_ratio per round, see module docstring."""
    need = [sum(math.ceil(c.hp / 3) for c in _pirates(f)) for f in level.filas]
    return [sum(need[max(0, i - 2): i + 1]) / (i + 1) for i in range(len(need))]


MIN_ARCHETYPE_SCORE = 0.35  # below this on every axis = no clear identity


def archetype_scores(level: Level) -> dict[str, float]:
    """How strongly the level expresses each archetype, 0..1. Replaced plain
    yes/no tags the same day: dense levels matched 4-5 tags at once, so
    "variety" measured on tags said every level was everything."""
    filas = level.filas
    pirates = [c for f in filas for c in _pirates(f)]
    if not pirates:
        return {name: 0.0 for name in ARCHETYPES}
    counts = [len(_pirates(f)) for f in filas]
    hps = [sum(c.hp for c in _pirates(f)) for f in filas]
    third = max(1, len(filas) // 3)
    total_hp = sum(c.hp for c in pirates)

    stacked = sum(1 for i in range(1, len(filas)) for c in _pirates(filas[i])
                  if any(p.index == c.index for p in _pirates(filas[i - 1])))
    # side of each fila's threat: R (columns 0-1) / L (3-4); a switch = the
    # dominant side flips between runs of >= 2 filas
    sides = ""
    for f in filas:
        right = sum(c.hp for c in _pirates(f) if c.index <= 1)
        left = sum(c.hp for c in _pirates(f) if c.index >= 3)
        sides += "R" if right > left else "L" if left > right else "-"
    flips = sum(1 for i in range(1, len(sides)) if {sides[i - 1], sides[i]} == {"R", "L"})
    long_runs = ("RR" in sides) + ("LL" in sides)

    def clip(x: float) -> float:
        return max(0.0, min(1.0, x))

    return {
        "swarm": clip(sum(c.hp <= 2 for c in pirates) / len(pirates) * statistics.mean(counts) / 3),
        "tank": clip(1.2 * sum(c.hp for c in pirates if c.hp >= 5) / total_hp),
        "wall": clip(1.5 * stacked / len(pirates)),
        "crescendo": clip((statistics.mean(hps[-third:]) / max(statistics.mean(hps[:third]), 0.01) - 1) / 2),
        "burst": clip((max(counts) - statistics.median(counts)) / 3),
        "switch": clip(long_runs / 2 * min(flips, 3) / 3),
    }


def primary_archetype(level: Level) -> str | None:
    """The single archetype the level expresses most (None = no clear idea).
    Variety is measured on this, one label per level."""
    scores = archetype_scores(level)
    name = max(scores, key=lambda a: (scores[a], -list(ARCHETYPES).index(a)))
    return name if scores[name] >= MIN_ARCHETYPE_SCORE else None


def detect_archetypes(level: Level) -> list[str]:
    """Every archetype the level expresses clearly (score >= threshold),
    strongest first. [] = no clear identity."""
    scores = archetype_scores(level)
    return sorted((a for a, s in scores.items() if s >= MIN_ARCHETYPE_SCORE), key=lambda a: -scores[a])


def pacing_report(level: Level) -> PacingReport:
    counts = [len(_pirates(f)) for f in level.filas]
    n = len(counts)
    ratios = demand_ratios(level)
    third = max(1, n // 3)
    report = PacingReport(
        filas=n,
        pirates_per_fila=round(sum(counts) / n, 2) if n else 0.0,
        single_pirate_share=round(sum(1 for c in counts if c == 1) / n, 2) if n else 1.0,
        late_demand=round(statistics.mean(ratios[-third:]), 2) if ratios else 0.0,
        archetypes=detect_archetypes(level),
        primary=primary_archetype(level),
    )
    if n > MAX_FILAS:
        report.problems.append(
            f"too long: {n} rounds (max {MAX_FILAS}) — long levels hand the player so many cannons "
            f"that the end is trivial")
    if report.single_pirate_share > MAX_SINGLE_PIRATE_SHARE:
        report.problems.append(
            f"{report.single_pirate_share:.0%} of rounds have a single pirate (max "
            f"{MAX_SINGLE_PIRATE_SHARE:.0%}) — spread the threat over several pirates/columns")
    if report.late_demand < MIN_LATE_DEMAND:
        report.problems.append(
            f"gets easier as it goes: last-third pressure {report.late_demand} (min {MIN_LATE_DEMAND}). "
            f"The player gains a cannon every round, so later rounds must bring MORE total danger "
            f"(more pirates per round, HP split across columns), not the same or less")
    return report


def least_represented(levels: list[Level], candidates: list[str] | None = None) -> str:
    """Archetype seen least among `levels` (ties -> ARCHETYPES order), so a
    generator can steer toward variety instead of repeating one shape."""
    names = candidates or list(ARCHETYPES)
    seen = Counter(primary_archetype(lv) for lv in levels)
    return min(names, key=lambda a: (seen[a], names.index(a)))

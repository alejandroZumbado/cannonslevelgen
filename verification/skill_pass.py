"""Skill pass (2026-10-07) — makes every release level do its JOB in the
campaign, judged by how much skill it really asks, not by its size.

Why: the audit's difficulty_score is ~0.3 x (pirates + max HP), i.e. content
size. Measured with a naive player instead (policy/baseline.py: puts each new
cannon on the most threatened column, merges onto undersized ones, NEVER
moves a placed cannon), the release had three problems:
  - the end got EASIER: the naive player won 18% of the bodies at positions
    251-300 but 34% at 451-500;
  - 8 arc peaks were won by the naive player (a peak that needs no skill);
  - the 44 breathers were near-copies of one shape (5-6 single HP-1 pirates
    in a line), 3 pairs >= 80% identical — relief, but no fun and no purpose.
Plus 6 arcs with one archetype in >= 60% of their levels.

Each targeted level gets one GOAL, met by a local search over the same small
edits the regulator uses (verification/level_mutations.py), every candidate
simulated — no LLM:
  - harden  (late bodies / peaks the naive player wins): the naive player
    must LOSE, the champion must still win (peaks: champion or solver), pacing
    ok, size within the level's own band;
  - reshape (repetitive arcs): reach a different archetype without becoming
    easier for the naive player;
  - breather: short (4-6 rounds), easy (naive wins) but not empty: several
    pirates per round, a pirate gets past the first row, HP <= 3 (<= 4 late),
    a rotating archetype and a shape no other level has.

Output: reports/regulation/skill/shard_0.json in the regulator's record
format — `python -m verification.apply_regulation --pass skill` writes the
assets. Levels whose goal wasn't met are recorded as "unresolved_skill" and
left untouched (learning/regulation_designer.py picks them up).

Run: python -m verification.skill_pass [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from policy.baseline import BaselinePolicy
from policy.loader import load_policy_from_file
from sim.engine import run_level
from sim.level import Level
from verification import pacing
from verification.level_mutations import OPERATORS, _free_columns, _new_pirate, normalize
from verification.official_levels import load_all
from verification.solver import solve_thoroughly, DEFAULT_MAX_ROUNDS

REPORT_DIR = config.ROOT / "reports" / "regulation" / "skill"
# every level shape in the game after this pass (regulation_designer runs
# without the Cannons checkout and needs it to keep breathers unique)
SIGNATURES_PATH = REPORT_DIR / "signatures.json"
RELEASE_ORDER_PATH =config.ROOT / "reports" / "release_order.json"
LEVELS_DIR = config.CANNONS_REPO / "Assets" / "Levels"

# Max share of arc bodies the naive player may win, by release position.
# Early on, winning by just placing cannons is fine (the player is learning).
# Bands of 100 (not one 301+ band): a wide band let 451-500 stay easy (34%)
# while 301-350 averaged it down.
NAIVE_BODY_CAP = ((100, 1.0), (200, 0.5), (300, 0.3), (400, 0.2), (500, 0.2), (10_000, 0.2))
REPETITIVE_SHARE = 0.6      # an arc with one archetype >= this share gets reshaped
RESHAPE_DOWN_TO = 0.5       # ... until that archetype is at most this share
RUN_LIMIT = 4               # this many consecutive levels with one archetype = a run to break
DESIGN_ARCHETYPES = ("swarm", "wall", "crescendo", "burst", "switch")  # tank: no generator reaches it
BREATHER_FILAS = (4, 6)
SIZE_BAND = (0.8, 1.35)     # pirates after/before, so a level keeps its place in the curve
MAX_EVALS = 500
MAX_SECONDS = 150
STALL_AFTER_GOAL = 30
SOLVER_WIDTHS = (200,)  # a win at width 200 is a real win; wider only adds minutes


# ---- evaluation ----------------------------------------------------------------

@dataclass
class SkillEval:
    champion_won: bool
    winnable: bool           # champion, or solver for peaks
    classification: str
    naive_won: bool
    tension: int             # furthest row a pirate reached in the champion's game
    pacing: dict
    primary: str | None
    pirates: int
    filas: int
    max_hp: int


class Evaluator:
    """Champion + naive player, cached by shape. The solver (peaks only) is
    slow, so it runs on demand via `confirm`, only for candidates that already
    beat the current best on everything else (the first version called it on
    every candidate and spent 20+ minutes on one peak)."""

    def __init__(self):
        self.champion = load_policy_from_file(config.ROOT / "policy" / "current.py")
        self.naive = BaselinePolicy()
        self.cache: dict[str, SkillEval] = {}
        self.solved: dict[str, bool] = {}

    def __call__(self, level: Level, allow_solver: bool = False) -> SkillEval:
        key = level.shape_signature()
        if key not in self.cache:
            champ = run_level(level, self.champion)
            pirates = [c for f in level.filas for c in f.cuadros if c.tipo >= 1]
            self.cache[key] = SkillEval(
                champion_won=champ.won, winnable=champ.won,
                classification="champion_win" if champ.won else "no_win_found",
                naive_won=run_level(level, self.naive).won,
                tension=champ.max_position_reached, pacing=asdict(pacing.pacing_report(level)),
                primary=pacing.primary_archetype(level), pirates=len(pirates),
                filas=len(level.filas), max_hp=max((c.hp for c in pirates), default=0))
        ev = self.cache[key]
        return self.confirm(level) if allow_solver and not ev.champion_won else ev

    def confirm(self, level: Level) -> SkillEval:
        """Same evaluation, with the solver deciding winnability when the
        champion loses."""
        ev = self(level)
        if ev.champion_won:
            return ev
        key = level.shape_signature()
        if key not in self.solved:
            self.solved[key] = solve_thoroughly(level, widths=SOLVER_WIDTHS, max_rounds=DEFAULT_MAX_ROUNDS).won
        won = self.solved[key]
        return SkillEval(**{**asdict(ev), "winnable": won,
                            "classification": "solved_by_search_only" if won else "no_win_found"})


# ---- goals -----------------------------------------------------------------------

@dataclass
class Goal:
    kind: str                      # harden | reshape | breather
    is_peak: bool = False
    target_archetype: str | None = None
    keep_naive_lost: bool = False  # reshape: may not become winnable by the naive player
    max_hp: int = 3                # breather only
    reason: str = ""
    # the archetype the level must NOT end as (the one repeating in its arc/run).
    # When set, any other clear archetype meets the goal; target_archetype then
    # only steers the search (2026-10-08: 29/313/384 reached a fine archetype,
    # just not the exact target, and were left unresolved)
    avoid_archetype: str | None = None


def penalty(goal: Goal, ev: SkillEval, original: SkillEval, taken: set[str], signature: str) -> float:
    """0 = goal met. Bigger = further away. Weights order the priorities:
    winnable > naive outcome > pacing > archetype/shape details."""
    p = 0.0
    if not ev.winnable or (not goal.is_peak and not ev.champion_won):
        p += 1000  # bodies/breathers must stay winnable by the champion (= a decent human)
    pace = ev.pacing
    if goal.kind == "breather":
        # relief level: easy on purpose, so late pressure isn't checked
        p += 300 * (not ev.naive_won)
        p += 60 * max(0.0, pace["single_pirate_share"] - pacing.MAX_SINGLE_PIRATE_SHARE) * 10
        lo, hi = BREATHER_FILAS
        p += 40 * (max(0, lo - ev.filas) + max(0, ev.filas - hi))
        p += 30 * max(0, ev.max_hp - goal.max_hp)
        p += 50 * (ev.tension < 1)
        p += 500 * (signature in taken)
    else:
        p += 50 * len(pace["problems"]) + 100 * max(0.0, pacing.MIN_LATE_DEMAND - pace["late_demand"])
        if goal.kind == "harden" or goal.keep_naive_lost:
            p += 300 * ev.naive_won
        lo, hi = SIZE_BAND
        p += 20 * max(0.0, lo * original.pirates - ev.pirates, ev.pirates - hi * original.pirates)
        p += 30 * max(0, ev.filas - max(original.filas + 1, 4))
        p += 50 * (ev.tension < 1)
    if goal.avoid_archetype:
        # None = no clear identity: doesn't repeat the arc, but isn't variety either
        p += 80 * (ev.primary in (goal.avoid_archetype, None))
    elif goal.target_archetype and ev.primary != goal.target_archetype:
        p += 80
    return p


def _changed_cells(a: Level, b: Level) -> int:
    cells = lambda lv: {(i, c.index, c.hp) for i, f in enumerate(lv.filas) for c in f.cuadros if c.tipo >= 1}
    return len(cells(a) ^ cells(b))


def add_any(level: Level, rng: random.Random) -> Level | None:
    """Adds an HP 1-2 pirate to ANY fila with a free column (breathers start as
    one-pirate lines; level_mutations only widens singles or late filas)."""
    import copy
    options = [i for i, f in enumerate(level.filas) if _free_columns(f)]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fila = lv.filas[rng.choice(options)]
    fila.cuadros.append(_new_pirate(rng, rng.choice(_free_columns(fila)), rng.randint(1, 2)))
    return normalize(lv)


def consolidate(level: Level, rng: random.Random) -> Level | None:
    """Two pirates of one fila become one with their summed HP (fewer, taller
    pirates). The only edit that lowers the "swarm" score (share of HP<=2
    pirates x pirates per fila) without making the level shorter or easier."""
    import copy
    options = [i for i, f in enumerate(level.filas) if len([c for c in f.cuadros if c.tipo >= 1]) >= 2]
    if not options:
        return None
    lv = copy.deepcopy(level)
    fila = lv.filas[rng.choice(options)]
    a, b = rng.sample([c for c in fila.cuadros if c.tipo >= 1], 2)
    a.hp += b.hp  # normalize clamps to the game's max HP
    fila.cuadros.remove(b)
    return normalize(lv)


_OPS = dict(OPERATORS, add_any=add_any, consolidate=consolidate)
# what each goal leans on (everything else keeps a small weight)
_GOAL_WEIGHTS = {
    # the naive player never MOVES cannons and merges only reactively: side
    # switches, blocking stacks and taller late pirates are what beat it
    "harden": {"move_column": 3, "stack": 3, "bump_late": 2, "grow": 2, "shift_later": 1, "add_late": 1},
    "reshape": {},
    "breather": {"add_any": 4, "widen_single": 3, "move_column": 2, "merge_filas": 1, "shave": 1},
}
_ARCHETYPE_WEIGHTS = {
    "swarm": {"split": 3, "widen_single": 2, "add_any": 2, "shave": 1},
    "wall": {"stack": 3, "move_column": 1},
    "crescendo": {"add_late": 3, "bump_late": 1, "shift_later": 1},
    "burst": {"shift_later": 3, "merge_filas": 2, "remove": 1},
    "switch": {"move_column": 3, "shift_later": 1},
}


def _weights(goal: Goal) -> dict[str, float]:
    w = {name: 0.3 for name in _OPS}
    for name, x in _GOAL_WEIGHTS[goal.kind].items():
        w[name] += x
    for name, x in _ARCHETYPE_WEIGHTS.get(goal.target_archetype or "", {}).items():
        w[name] += x
    if goal.avoid_archetype == "swarm":
        # leaving swarm = fewer, taller pirates; adding pirates only deepens it
        w["consolidate"] += 3
        w["grow"] += 1
    return w


def search(level: Level, goal: Goal, evaluate: Evaluator, taken: set[str], rng: random.Random):
    """Hill climb on penalty (ties broken by fewer changed cells). Sideways
    moves are allowed while the goal is unmet, to walk off plateaus."""
    original = normalize(level)
    orig_ev = evaluate(original, goal.is_peak)
    best, best_ev = original, orig_ev
    def tie_break(cand: Level, ev: SkillEval) -> int:
        # fewer edits first; a level that wasn't asked to change archetype
        # should keep its own (so hardening doesn't undo the arc's variety)
        drift = goal.target_archetype is None and ev.primary != orig_ev.primary
        return _changed_cells(original, cand) + 10 * drift

    best_key = (penalty(goal, best_ev, orig_ev, taken, best.shape_signature()), 0)
    weights = _weights(goal)
    started, evals, stall = time.monotonic(), 0, 0
    while evals < MAX_EVALS and time.monotonic() - started < MAX_SECONDS:
        if best_key[0] == 0 and stall >= STALL_AFTER_GOAL:
            break
        name = rng.choices(list(weights), weights=list(weights.values()))[0]
        cand = _OPS[name](best, rng)
        if cand is None:
            stall += 1
            continue
        evals += 1
        ev = evaluate(cand)
        sig = cand.shape_signature()
        if goal.is_peak and not ev.champion_won:
            # optimistic first (as if the solver wins); pay for the solver
            # only if that would beat the current best
            optimistic = SkillEval(**{**asdict(ev), "winnable": True})
            opt_key = (penalty(goal, optimistic, orig_ev, taken, sig), tie_break(cand, optimistic))
            if opt_key[0] > best_key[0] or (best_key[0] == 0 and opt_key >= best_key):
                stall += 1
                continue
            ev = evaluate.confirm(cand)
        key = (penalty(goal, ev, orig_ev, taken, sig), tie_break(cand, ev))
        if key < best_key or (best_key[0] > 0 and key[0] == best_key[0]):
            improved = key < best_key
            best, best_ev, best_key = cand, ev, key
            stall = 0 if improved else stall + 1
        else:
            stall += 1
    return best, orig_ev, best_ev, best_key[0], evals


# ---- planning --------------------------------------------------------------------

def plan(order: list[dict], levels: dict[int, Level], evaluate: Evaluator) -> dict[int, Goal]:
    """Which release levels need which goal (see module docstring)."""
    rows = []
    for pos, e in enumerate(order, 1):
        lv = levels[e["levelNumber"]]
        ev = evaluate(lv, allow_solver=False)
        rows.append({"pos": pos, "arc": e["arc"], "role": e["role"], "n": e["levelNumber"], "ev": ev})
    goals: dict[int, Goal] = {}

    # 1. peaks the naive player wins (arc 1 is the tutorial arc)
    for r in rows:
        if r["role"] == "peak" and r["arc"] > 1 and r["ev"].naive_won:
            goals[r["n"]] = Goal("harden", is_peak=True, reason=f"peak at {r['pos']} won by the naive player")

    # 2. bodies: cap the naive-winnable share per position band; harden the
    # ones nearest their arc's peak first, so each arc still ramps
    lo = 0
    by_arc = defaultdict(list)
    for r in rows:
        by_arc[r["arc"]].append(r)
    for hi, cap in NAIVE_BODY_CAP:
        band = [r for r in rows if lo < r["pos"] <= hi and r["role"] == "body"]
        lo = hi
        excess = sum(r["ev"].naive_won for r in band) - int(cap * len(band))
        if excess <= 0:
            continue
        naive = [r for r in band if r["ev"].naive_won]
        naive.sort(key=lambda r: -by_arc[r["arc"]].index(r) / len(by_arc[r["arc"]]))
        for r in naive[:excess]:
            goals[r["n"]] = Goal("harden", reason=f"body at {r['pos']}: band <= {hi} over its naive cap {cap:.0%}")

    # 3. repetitive arcs and long single-archetype runs -> reshape some bodies
    for arc, members in by_arc.items():
        bodies = [r for r in members if r["role"] == "body"]
        counts = Counter(r["ev"].primary for r in members)
        top, cnt = counts.most_common(1)[0]
        if top is None or cnt / len(members) < REPETITIVE_SHARE:
            continue
        need = cnt - int(RESHAPE_DOWN_TO * len(members))
        for r in [b for b in bodies if b["ev"].primary == top][::-1][:need]:
            target = min(DESIGN_ARCHETYPES, key=lambda a: (counts[a], DESIGN_ARCHETYPES.index(a)))
            counts[target] += 1
            _add_reshape(goals, r, target, f"arc {arc}: {top} in {cnt}/{len(members)} levels")
    run = [rows[0]]
    for r in rows[1:] + [None]:
        if r is not None and r["ev"].primary == run[-1]["ev"].primary:
            run.append(r)
            continue
        if len(run) >= RUN_LIMIT:
            mid = [x for x in run[1:-1] if x["role"] == "body"]
            if mid and not any(x["n"] in goals and goals[x["n"]].target_archetype for x in run):
                pick = mid[len(mid) // 2]
                target = next(a for a in DESIGN_ARCHETYPES if a != run[0]["ev"].primary)
                _add_reshape(goals, pick, target, f"run of {len(run)} {run[0]['ev'].primary} at {run[0]['pos']}")
        run = [r] if r is not None else []

    # 4. breathers: rotate archetypes, contrast with the arc they open
    used = Counter()
    for r in rows:
        if r["role"] != "breather":
            continue
        arc_top = Counter(x["ev"].primary for x in by_arc[r["arc"]] if x["role"] == "body").most_common(1)[0][0]
        target = min((a for a in DESIGN_ARCHETYPES if a != arc_top), key=lambda a: (used[a], DESIGN_ARCHETYPES.index(a)))
        used[target] += 1
        goals[r["n"]] = Goal("breather", target_archetype=target, max_hp=3 if r["arc"] <= 10 else 4,
                             reason=f"breather at {r['pos']}", avoid_archetype=arc_top)
    return goals


def _add_reshape(goals: dict[int, Goal], r: dict, target: str, reason: str) -> None:
    g = goals.get(r["n"])
    if g is not None:  # already hardened: also steer its archetype
        g.target_archetype, g.reason = target, f"{g.reason}; {reason}"
        g.avoid_archetype = r["ev"].primary
        return
    goals[r["n"]] = Goal("reshape", target_archetype=target, keep_naive_lost=not r["ev"].naive_won,
                         reason=reason, avoid_archetype=r["ev"].primary)


# ---- runner ----------------------------------------------------------------------

def _solve(level: Level, goal: Goal, evaluate: Evaluator, taken: set[str], seeds: int):
    """Searches the goal with up to `seeds` random restarts (a hill climb
    stuck on one plateau often clears it from another start); for breather/
    reshape goals, each other steering archetype is tried too. Returns the
    goal actually met (or the original one) and the best search result."""
    n = level.levelNumber
    steer = [goal.target_archetype]
    if goal.kind in ("breather", "reshape"):
        # the archetype is the usual blocker (a 4-6 round "swarm" breather
        # needs ~20 pirates): try the other ones before giving up
        steer += [a for a in DESIGN_ARCHETYPES if a not in (goal.target_archetype, goal.avoid_archetype)]
    best_goal, best = goal, None
    for seed in range(max(1, seeds)):
        for target in steer:
            trial = Goal(**{**asdict(goal), "target_archetype": target})
            result = search(level, trial, evaluate, taken, random.Random(n + 1000 * seed))
            if best is None or result[3] < best[3]:
                best_goal, best = trial, result
            if best[3] == 0:
                return best_goal, best
    return best_goal, best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Skill pass over the release levels.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument("--limit", type=int, default=0, help="process only the first N planned levels")
    parser.add_argument("--levels", default="", help="comma-separated levelNumbers: retry only these planned levels")
    parser.add_argument("--seeds", type=int, default=1, help="random restarts per goal (default 1)")
    args = parser.parse_args(argv)

    order = json.loads(RELEASE_ORDER_PATH.read_text(encoding="utf-8"))["order"]
    levels = {lv.levelNumber: lv for lv in load_all(LEVELS_DIR)}
    missing = [e["levelNumber"] for e in order if e["levelNumber"] not in levels]
    if missing:
        raise SystemExit(f"release levels without an asset: {missing[:10]}")
    evaluate = Evaluator()
    goals = plan(order, levels, evaluate)
    if args.levels:
        wanted = {int(x) for x in args.levels.split(",") if x.strip()}
        unplanned = wanted - set(goals)
        if unplanned:
            # not an error: the level may already meet every goal now
            print(f"not in the plan (nothing to do): {sorted(unplanned)}")
        goals = {n: g for n, g in goals.items() if n in wanted}
    kinds = Counter(g.kind for g in goals.values())
    print(f"plan: {len(goals)} levels {dict(kinds)}")
    if args.dry_run:
        for n, g in goals.items():
            print(f"  L{n}: {g.kind}{' peak' if g.is_peak else ''} -> {g.target_archetype or '-'} ({g.reason})")
        return 0

    # every shape already in the game: a breather may not copy any of them
    taken = {lv.shape_signature() for lv in levels.values()}
    records, items = [], list(goals.items())[: args.limit or None]
    for i, (n, goal) in enumerate(items, 1):
        level = levels[n]
        taken.discard(level.shape_signature())
        goal, (best, before, after, left, evals) = _solve(level, goal, evaluate, taken, args.seeds)
        best.levelNumber, best.password, best.isHard = level.levelNumber, level.password, level.isHard
        met = left == 0
        taken.add((best if met else level).shape_signature())
        records.append({
            "levelNumber": n, "status": "regulated" if met else "unresolved_skill",
            "goal": asdict(goal), "before": asdict(before), "after": asdict(after),
            "penalty_left": round(left, 1), "evaluations": evals,
            "changed_cells": _changed_cells(normalize(level), best), "level": best.to_dict(),
            "original": level.to_dict(),  # what regulation_designer starts from if unmet
            # started from the current asset (already holding any applied LLM
            # proposal): apply_regulation must not re-apply those over it
            "built_on_assets": True,
        })
        print(f"  [{i}/{len(items)}] L{n} {goal.kind}: {'OK' if met else f'unmet ({left:.0f})'} "
              f"naive {before.naive_won}->{after.naive_won} {before.primary}->{after.primary} "
              f"pirates {before.pirates}->{after.pirates} ({evals} evals)", flush=True)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    SIGNATURES_PATH.write_text(json.dumps(sorted(taken)), encoding="utf-8")
    # merge with earlier runs: a level fixed in run 1 but not planned in run 2
    # must stay recorded (apply_regulation protects recorded levels from older passes)
    shard = REPORT_DIR / "shard_0.json"
    merged = {r["levelNumber"]: r for r in json.loads(shard.read_text(encoding="utf-8"))["levels"]} \
        if shard.exists() else {}
    for r in records:
        if r["status"] == "regulated" or merged.get(r["levelNumber"], {}).get("status") != "regulated":
            merged[r["levelNumber"]] = r
    shard.write_text(json.dumps({"levels": list(merged.values())}, indent=1), encoding="utf-8")
    done = Counter((r["goal"]["kind"], r["status"]) for r in records)
    print(f"skill pass: {dict(done)} -> {REPORT_DIR / 'shard_0.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Month-2+ entry point: one level per day, cheap. Uses the policy and rules
learned during month 1 instead of spending a full day's tokens per level.
Scheduled daily since 2026-09-13 (see daily_production.yml) now that the
learning phase has produced a policy trusted to run unattended.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
import re
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
import incident_log
from llm import client, audit
from llm.budget import BudgetExceeded
from llm.client import ProviderQuotaExhausted
from learning.game_rules import GAME_RULES
from policy.loader import load_policy_from_file, PolicyLoadError
from production.cannons_sync import push_generated_level
from production.level_registry import scan_existing_levels, scan_existing_signatures, ensure_unique_password
from sim.benchmark import fixed_suite, random_suite
from sim.engine import run_level
from sim.evaluate import evaluate
from sim.level import Level, fill_empty_filas
from policy.baseline import BaselinePolicy
from verification import pacing
from verification.drag import needs_drag
from verification.level_audit import audit_level
from verification.official_levels import load_all
from verification.solver import ESCALATION_BEAM_WIDTHS, DEFAULT_MAX_ROUNDS, solve_thoroughly

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"
# 5 -> 8 (2026-09-24): the pacing gate rejects more candidates, and each retry now
# carries the rejection reason; budget freed by pausing strategy_learner covers it.
MAX_ATTEMPTS = 8
RECENT_LEVELS_FOR_VARIETY = 20  # archetype mix is judged on the newest N levels

# More levels per run (2026-10-14): the run used ~3k of the 180k daily tokens
# (strategy_learner paused, designer idle) — the user asked to spend the slack
# on smarter, harder levels. One variety level (least-used archetype, as
# before) + DRAG_LEVELS_PER_RUN levels that REQUIRE dragging a placed cannon
# (verification/drag.py) + EXTREME_LEVELS_PER_RUN (below). Worst case
# 3 x 8 attempts x ~3k = ~72k tokens.
DRAG_LEVELS_PER_RUN = int(os.environ.get("DRAG_LEVELS_PER_RUN", 1))
# + an EXTREME level (same day, user request): at the limits — the trained AI
# loses, the exhaustive solver wins, the naive player loses. Variety stays: the
# run is variety + drag + extreme, never three of one kind.
EXTREME_LEVELS_PER_RUN = int(os.environ.get("EXTREME_LEVELS_PER_RUN", 1))
EXTREME_FOCUS = "extreme"
EXTREME_BRIEF = (
    "the level must be EXTREME but winnable: sit right at the LIMITS in the rules (one HP more and no "
    "line of play wins). A strong trained AI must LOSE it while an exhaustive search still finds the "
    "single winning line. Use competing threats: tall pirates that need merges prepared rounds early, "
    "blocking stacks, and pressure that forces a drag at the right moment"
)
DRAG_FOCUS = "drag"  # pseudo-archetype: a sim property, not a structural shape in pacing.ARCHETYPES
DRAG_BRIEF = (
    "the level must be IMPOSSIBLE without dragging an already placed cannon to another column "
    "(checked by a solver that may only drop new cannons). Recipe: steady low-HP pressure in one or "
    "two columns makes the player build merged damage THERE, then a tall pirate (HP 6-10) appears in "
    "a column that had little or no pressure — too tall for the cannons a player can still drop there "
    "in its 3 shots, so the merged cannon must be dragged over. Keep pressure on the original columns "
    "too, so dragging is a real trade-off, not free"
)
# needs_drag's spawn-only search is cheap (5 actions/state); two tiers are enough here
DRAG_CHECK_WIDTHS = (200, 800)

# Gate added 2026-09-05: this script only ever validated the ONE level it
# just generated against the current policy (engine.won) — it never checked
# whether the policy itself is any good in aggregate. A policy that regressed
# (a bad promotion slipping through strategy_learner, or a bug) could still
# generate and push a "valid" level built around a weak strategy, straight
# into the real game, with nothing to stop it. Reusing the same benchmark
# suite/threshold-free style strategy_learner already uses for promotions —
# if the champion can't clear this bar, don't generate anything today rather
# than ship a level tuned to a policy you wouldn't have promoted.
CHAMPION_WIN_RATE_THRESHOLD = 0.85

_LEVEL_SCHEMA = """\
Level JSON schema:
{
  "levelNumber": <int>, "password": "<1 uppercase letter + 4 digits>", "isHard": <bool>,
  "filas": [{"cuadros": [{"index": <0-4>, "tipo": <1-4>, "hp": <1-10>}, ...]}, ...]
}
tipo 1-3 = normal pirate (skin variety only), tipo 4 = last pirate of the level
(use exactly once, on the final fila with pirates). index = column, 0=right..4=left.
"""


# Pacing guidance (2026-09-24, see verification/pacing.py for the why and the
# numbers). Replaces the old "include at least one empty breathing-room fila":
# fill_empty_filas turns every empty fila into a lone HP-1 pirate, so that
# instruction was literally manufacturing boring single-pirate rounds.
_PACING_RULES = f"""\
PACING — the level is rejected automatically if it breaks these:
- The player gains ONE new cannon every round, so their firepower grows
  every round. A level whose later rounds are no heavier than its early ones
  gets EASIER as it goes. Later rounds must bring more total danger than
  earlier ones: more pirates per round, spread over more columns.
- Prefer width over height: spread a big threat over several pirates in
  different columns (2 pirates of HP 7 in two columns beat 1 pirate of HP 14,
  which is also illegal: HP max is 10).
- At most {pacing.MAX_SINGLE_PIRATE_SHARE:.0%} of the filas may have a single pirate.
- At most {pacing.MAX_FILAS} filas. Short and dense beats long and sparse.
- Every fila must have at least one pirate (no empty filas).
- It must ask for skill: a player who only drops each new cannon on the most
  threatened column and never moves a placed cannon must LOSE (checked by
  simulation). Moving cannons between sides, blocked pirates and merges
  prepared in advance are what a good player needs.
"""


def _build_prompt(level_number: int, archetype: str, feedback: str | None) -> tuple[str, str]:
    system = (
        "You design one winnable, well-paced level for the tower-defense game "
        "Cannons. Ground everything in the real rules below — never invent a "
        "mechanic that isn't stated there, no matter how plausible it sounds."
    )
    # the retry note is how one run "learns": the next attempt sees exactly why
    # the previous one was rejected instead of rolling the dice again blind
    retry_note = f"\nYOUR PREVIOUS ATTEMPT WAS REJECTED: {feedback}\nFix exactly that.\n" if feedback else ""
    # The learned-rules block (knowledge.rules_as_prompt_block) was dropped
    # 2026-09-24, same as strategy_learner did on 09-22: by then it mostly
    # restated GAME_RULES, and one "confirmed" rule shown every day claimed a
    # blocker grants extra shooting rounds, contradicting GAME_RULES' 3-shot cap.
    # drag levels get the drag recipe instead of a structural archetype
    if archetype == DRAG_FOCUS:
        target = f"Design level number {level_number} around the DRAG mechanic:\n  {DRAG_BRIEF}"
    elif archetype == EXTREME_FOCUS:
        target = f"Design level number {level_number} as an EXTREME level:\n  {EXTREME_BRIEF}"
    else:
        target = (f"Design level number {level_number} around this archetype (the least used in\n"
                  f"the game's recent levels, so the campaign stays varied):\n"
                  f"  {archetype}: {pacing.ARCHETYPES[archetype]}")
    user = f"""{GAME_RULES}

{_LEVEL_SCHEMA}

{_PACING_RULES}
{target}
{retry_note}
Respond with a one-sentence design note, then the JSON in a single ```json code block.
"""
    return system, user


def _extract_json(text: str) -> dict | None:
    match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _try_repair(level: Level, archetype: str, existing_signatures: set[str], attempt: int) -> Level | None:
    """production/level_repair.py on a rejected candidate; None if it can't be
    saved. Never raises: a repair bug must not cost the day's other attempts."""
    from production import level_repair  # local import: pulls in the solver-free skill_pass stack
    # skill_pass only knows structural archetypes; a drag level is repaired toward any shape
    target = None if archetype in (DRAG_FOCUS, EXTREME_FOCUS) else archetype
    try:
        # drag levels are hardened toward needing a drag, not just toward beating the naive player
        kind = "drag" if archetype == DRAG_FOCUS else "harden"
        fixed = level_repair.repair(level, target, existing_signatures, seed=attempt, goal_kind=kind)
    except Exception:  # noqa: BLE001 — logged as an incident, then treated as "not repaired"
        incident_log.record_exception("daily_generator.level_repair")
        print(f"  attempt {attempt}: local repair crashed (incident logged), treating as not repaired")
        return None
    print(f"  attempt {attempt}: local repair {'succeeded' if fixed else 'failed'}")
    return fixed


# local pushes take up to ~150 s each (skill_pass search). 3 -> every attempt the same day:
# the first test had pacing rejects in 2 of the first 3 attempts, so a cap of 3 left the
# push almost unused, while replaying the later unwinnable candidates, it fixed 3/3.
EXTREME_PUSH_ATTEMPTS = MAX_ATTEMPTS


def _push_to_extreme(level: Level, existing_signatures: set[str], attempt: int) -> Level | None:
    """Local hill climb (verification/skill_pass, goal "extreme") from a candidate the
    trained AI wins to one it loses and the solver still wins. None if it can't, or
    on any crash (logged): a push bug must not cost the day's other attempts."""
    import random
    from verification import skill_pass  # local import: heavy, only for extreme candidates
    try:
        goal = skill_pass.Goal("extreme", is_peak=True)
        best, _, after, left, _ = skill_pass.search(level, goal, skill_pass.Evaluator(), existing_signatures,
                                                    random.Random(attempt))
    except Exception:  # noqa: BLE001 — logged as an incident, then treated as "not pushed"
        incident_log.record_exception("daily_generator.push_to_extreme")
        return None
    ok = left == 0 and best.shape_signature() not in existing_signatures and after.winnable
    print(f"  attempt {attempt}: local push to extreme {'succeeded' if ok else 'failed'}")
    if not ok:
        return None
    best.levelNumber, best.password, best.isHard = level.levelNumber, level.password, level.isHard
    return best


def generate_one_level(level_number: int, used_passwords: set[str], existing_signatures: set[str],
                       archetype: str) -> Level | None:
    """Returns None if every attempt was tried and none was winnable (by the
    learned policy or, failing that, the solver) — a real, expected outcome (see main()), NOT the same thing as
    PolicyLoadError below, which callers should treat as an actual failure
    (there's no point retrying level generation if the policy itself is
    broken) and is deliberately left to propagate rather than being caught
    here.

    `level_number` and `used_passwords` come from level_registry.py scanning
    the REAL Assets/Levels/ contents — don't trust the LLM's own levelNumber
    field (it has no reliable way to know what's already there, and got this
    badly wrong for real on the first cloud run: see level_registry.py's
    docstring). `existing_signatures` (also from level_registry.py) is the
    set of shape hashes for every level that already exists — rejecting a
    match is a pure local check (no LLM call), same cost as any other retry."""
    policy = load_policy_from_file(CURRENT_POLICY_PATH)  # raises PolicyLoadError - let it propagate

    feedback: str | None = None  # why the previous attempt was rejected, shown to the next one
    for attempt in range(1, MAX_ATTEMPTS + 1):
        system, user = _build_prompt(level_number, archetype, feedback)
        feedback = None
        # reasoning_effort="low" (2026-09-22): 9/10 no_json attempts since
        # 09-16 were EMPTY responses at the max_tokens ceiling (hidden
        # reasoning used all 3000), which left 09-19 and 09-22 with no level.
        completion = client.complete(system, user, max_tokens=3000, reasoning_effort="low")
        response = completion.text
        level_dict = _extract_json(response)
        if level_dict is None:
            print(f"  attempt {attempt}: no JSON in response, retrying")
            audit.record_call(caller="daily_generator", completion=completion, system=system, user=user,
                               outcome={"accepted": False, "reason": "no_json", "attempt": attempt})
            continue
        try:
            level = Level.from_dict(level_dict)
        except (KeyError, TypeError) as e:
            print(f"  attempt {attempt}: schema error {e}, retrying")
            audit.record_call(caller="daily_generator", completion=completion, system=system, user=user,
                               outcome={"accepted": False, "reason": f"schema_error: {e}", "attempt": attempt})
            continue

        # The prompt asks for hp 1-10 but nothing enforced it — Level 508
        # (2026-09-23) came back with hp=12 and was accepted, since the sim
        # doesn't care. Reject out-of-range fields like any other bad candidate.
        range_errors = level.structure_errors()
        if range_errors:
            feedback = f"out-of-range fields: {'; '.join(range_errors[:3])}"
            print(f"  attempt {attempt}: out-of-range fields {range_errors[:3]}, retrying")
            audit.record_call(caller="daily_generator", completion=completion, system=system, user=user,
                               outcome={"accepted": False, "reason": f"out_of_range: {range_errors[:3]}",
                                        "attempt": attempt})
            continue

        # No shipped level should have a fila that spawns zero pirates — a
        # dead round the player just waits through (found 2026-09-15 in
        # 310/500 real levels). Filled here, before shape_signature() and
        # the winnability check below, so a duplicate-shape reject and the
        # accept/reject decision both see the level the game will actually
        # ship — a level rejected because the fill made it unwinnable is
        # just another retry, same as any other invalid candidate.
        level = fill_empty_filas(level)

        # Fun gate (2026-09-24): winnable isn't enough — reject levels that
        # get easier as they go, are mostly single-pirate rounds, or drag on.
        # Checked after the fill, on the level the game would actually ship.
        pacing_check = pacing.pacing_report(level)
        if not pacing_check.ok:
            feedback = " | ".join(pacing_check.problems)
            print(f"  attempt {attempt}: pacing rejected ({len(pacing_check.problems)} problem(s)), retrying")
            audit.record_call(caller="daily_generator", completion=completion, system=system, user=user,
                               outcome={"accepted": False, "reason": "pacing", "attempt": attempt,
                                        "problems": pacing_check.problems})
            continue

        signature = level.shape_signature()
        if signature in existing_signatures:
            print(f"  attempt {attempt}: duplicate of an existing level (same shape, different skin), retrying")
            audit.record_call(caller="daily_generator", completion=completion, system=system, user=user,
                               outcome={"accepted": False, "reason": "duplicate_level", "attempt": attempt})
            continue

        # enforce, don't just ask nicely: the level number is ours to assign
        # (the LLM's guess is discarded), and the password only gets replaced
        # if it actually collides with something already in the game.
        level.levelNumber = level_number
        level.password = ensure_unique_password(level.password, used_passwords)

        # Skill gate (2026-10-07): 7 of the first 12 daily levels were won by
        # the naive baseline (never moves a cannon, merges only reactively) —
        # e.g. 594 is 26 HP-1 pirates. The release already has plenty of easy
        # filler (breathers, early arcs); a daily level must ask for a decision.
        repaired_from = None  # "trivial" | "unwinnable" when level_repair rewrote the candidate
        if run_level(level, BaselinePolicy()).won:
            # local repair first (2026-10-08): keeps the idea, adds the decision
            fixed = _try_repair(level, archetype, existing_signatures, attempt)
            if fixed is not None:
                level, repaired_from = fixed, "trivial"
        if repaired_from is None and run_level(level, BaselinePolicy()).won:
            feedback = ("too easy: a player who just drops each new cannon on the most threatened column "
                        "and never moves a placed cannon wins it. Keep the idea but make it ask for a real "
                        "decision: pressure that switches sides (cannons must be MOVED), a pirate blocked by "
                        "another in its column, or a pirate tall enough that a merge must be prepared rounds earlier")
            print(f"  attempt {attempt}: trivial for the naive baseline, retrying")
            audit.record_call(caller="daily_generator", completion=completion, system=system, user=user,
                               outcome={"accepted": False, "reason": "trivial_for_baseline", "attempt": attempt})
            continue

        engine = run_level(level, policy)
        if not engine.won and archetype != EXTREME_FOCUS:  # extreme: never soften toward an AI win
            # local repair before the slow solver (2026-10-08): 14/16 candidates
            # of 10-07..08 died here; offline, repair saved 15/16 in ~1 s each
            fixed = _try_repair(level, archetype, existing_signatures, attempt)
            if fixed is not None:
                level, repaired_from = fixed, "unwinnable"
                engine = run_level(level, policy)
        # Policy loss -> ask the solver (2026-09-26). The policy wins 0/35 of the
        # real winnable levels it's trained on, all merge-heavy, so "tank" (the
        # archetype _pick_archetype keeps choosing, 0 in the campaign) was
        # rejected 16/16 times on 09-25..26 and nothing shipped. What players
        # need is a winnable level; the campaign already ships 35
        # solved_by_search_only levels by the same standard (level_audit.py).
        solver_won = False if engine.won else solve_thoroughly(level, ESCALATION_BEAM_WIDTHS,
                                                               DEFAULT_MAX_ROUNDS).won
        # Extreme gate (2026-10-14): the trained AI must lose, the solver win. The
        # LLM tends to overshoot (first test: 6/8 candidates unwinnable even for the
        # solver), so a miss gets a local fix first: soften an unwinnable one until
        # the AI wins (level_repair.soften), then push it back up to the limits.
        if (archetype == EXTREME_FOCUS and attempt <= EXTREME_PUSH_ATTEMPTS
                and (engine.won or not solver_won)):
            import random
            from production import level_repair
            start = level if engine.won else level_repair.soften(level, policy, random.Random(attempt))
            pushed = _push_to_extreme(start, existing_signatures, attempt) if start is not None else None
            if pushed is not None:
                level, repaired_from = pushed, "pushed_extreme"
                engine, solver_won = run_level(level, policy), True  # push only returns solver-won levels
        accepted = engine.won or solver_won
        extreme_missing = accepted and archetype == EXTREME_FOCUS and engine.won
        if extreme_missing:
            accepted = False
        # Drag gate (2026-10-14): a drag level must be unwinnable for spawn-only play.
        # Checked last — only on candidates that already passed every other gate.
        drag_missing = accepted and archetype == DRAG_FOCUS and not needs_drag(level, DRAG_CHECK_WIDTHS)
        if drag_missing:
            accepted = False
        outcome = {
            "accepted": accepted, "attempt": attempt, "rounds_played": engine.rounds_played,
            "level_password": level.password, "policy_won": engine.won, "solver_won": solver_won,
            "repaired_from": repaired_from, "focus": archetype, "drag_missing": drag_missing,
            "extreme_missing": extreme_missing,
        }
        audit.record_call(caller="daily_generator", completion=completion, system=system, user=user, outcome=outcome)
        if accepted:
            winner = "learned policy" if engine.won else "solver only (policy lost)"
            if repaired_from:
                winner += f" after local repair ({repaired_from})"
            print(f"  attempt {attempt}: winnable by {winner}, accepted")
            return level
        if extreme_missing:
            feedback = ("not extreme: the trained AI wins it. Push it to the LIMITS — taller late pirates that "
                        "need merges prepared early, denser last rounds, a blocking stack — until only one precise "
                        "line of play survives")
            print(f"  attempt {attempt}: winnable but not extreme (the trained AI wins), retrying")
            continue
        if drag_missing:
            feedback = ("winnable WITHOUT ever dragging a placed cannon — the drag isn't needed. Make the late "
                        "tall pirate's column one the player has no reason to fill beforehand, and raise its HP "
                        "so cannons dropped there in its 3 shots can't kill it; keep the merged damage "
                        "elsewhere so dragging it over is the only answer")
            print(f"  attempt {attempt}: winnable but needs no drag, retrying")
            continue
        feedback = ("neither the trained AI nor an exhaustive search could win it — too hard. Keep the "
                    "same idea but lower the single hardest spike (one HP, or one pirate in the densest round)")
        print(f"  attempt {attempt}: not winnable (policy and solver both lost), retrying")

    return None


# Archetype bench (2026-09-26): "tank" is the least-represented archetype, so
# _pick_archetype chose it every day and all 16 candidates of 09-25..26 were
# unwinnable even for the solver — production stalled with nothing to rotate
# to. An archetype whose last BENCH_AFTER_FAILED_RUNS runs produced nothing is
# skipped for BENCH_DAYS, then gets another chance.
ARCHETYPE_STATE_PATH = config.ROOT / "state" / "archetype_failures.json"
BENCH_AFTER_FAILED_RUNS = 2
BENCH_DAYS = 7


def _load_archetype_failures() -> dict:
    """{archetype: {"failed_runs": int, "last_failed": "YYYY-MM-DD"}}; {} if
    missing or unreadable (logged — a bad file must not stop production)."""
    if not ARCHETYPE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(ARCHETYPE_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  archetype state unreadable ({e}) — starting without a bench")
        return {}


def _record_archetype_result(archetype: str, produced: bool) -> None:
    """Success resets the archetype's streak; a run with no level extends it."""
    state = _load_archetype_failures()
    if produced:
        state.pop(archetype, None)
    else:
        entry = state.get(archetype, {"failed_runs": 0})
        entry["failed_runs"] += 1
        entry["last_failed"] = datetime.now(timezone.utc).date().isoformat()
        state[archetype] = entry
    ARCHETYPE_STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _benched_archetypes() -> set[str]:
    today = datetime.now(timezone.utc).date()
    benched = set()
    for name, entry in _load_archetype_failures().items():
        try:
            age = (today - datetime.fromisoformat(entry["last_failed"]).date()).days
        except (KeyError, ValueError):
            continue
        if entry.get("failed_runs", 0) >= BENCH_AFTER_FAILED_RUNS and age < BENCH_DAYS:
            benched.add(name)
    return benched


def _pick_archetype() -> str:
    """Least-represented archetype among the newest levels (Assets/Levels +
    pending incoming drops), so consecutive days don't keep producing the same
    kind of level. Benched archetypes (see above) are skipped unless every one
    is benched. Falls back to the first archetype if nothing can be read."""
    levels = []
    assets_dir = config.CANNONS_REPO / "Assets" / "Levels"
    if assets_dir.exists():
        levels.extend(load_all(assets_dir))
    for json_file in sorted(config.INCOMING_LEVELS_DIR.glob("*.json")) if config.INCOMING_LEVELS_DIR.exists() else []:
        try:
            levels.append(Level.load(json_file))
        except (ValueError, KeyError) as e:
            print(f"  variety: skipping unreadable {json_file.name} ({e})")
    recent = sorted(levels, key=lambda lv: lv.levelNumber)[-RECENT_LEVELS_FOR_VARIETY:]
    benched = _benched_archetypes()
    candidates = [a for a in pacing.ARCHETYPES if a not in benched] or None  # None = all
    if benched:
        print(f"  variety: skipping benched archetype(s) {sorted(benched)}")
    return pacing.least_represented(recent, candidates)


def _publish_level(level: Level, policy_check) -> None:
    """Writes the level to incoming/, runs the difficulty audit (champion,
    solver, naive baseline, drag check — all local simulation, no LLM) into
    reports/production_audit/, and pushes the JSON to the Cannons repo."""
    out_path = config.INCOMING_LEVELS_DIR / f"Level_{level.levelNumber}_{datetime.now():%Y%m%d}.json"
    level.save(out_path)
    print(f"wrote {out_path}")

    # Difficulty validation — same solver/scoring as the weekly audit, plus the
    # naive baseline: does the level need what the AI learned, or not?
    difficulty = audit_level(level, policy_check, ESCALATION_BEAM_WIDTHS, DEFAULT_MAX_ROUNDS)
    baseline_engine = run_level(level, BaselinePolicy())
    difficulty_tag = "trivial_for_baseline" if baseline_engine.won else "requires_learned_strategy"
    drag_required = needs_drag(level, DRAG_CHECK_WIDTHS)
    print(f"difficulty check: champion={difficulty.classification} score={difficulty.difficulty_score} "
          f"baseline_won={baseline_engine.won} ({difficulty_tag}) needs_drag={drag_required}")

    audit_dir = config.ROOT / "reports" / "production_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"Level_{level.levelNumber}_{datetime.now():%Y%m%d_%H%M%S}.json"
    audit_path.write_text(json.dumps({
        "levelNumber": level.levelNumber,
        "password": level.password,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "champion_classification": difficulty.classification,
        "champion_difficulty_score": difficulty.difficulty_score,
        "champion_rounds_played": difficulty.champion_rounds_played,
        "baseline_won": baseline_engine.won,
        "baseline_rounds_played": baseline_engine.rounds_played,
        "difficulty_tag": difficulty_tag,
        "needs_drag": drag_required,  # 2026-10-14: spawn-only solver can't win
        # pacing/variety (2026-09-24): lets reviews see WHY a level is (not) fun
        "pacing": asdict(pacing.pacing_report(level)),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {audit_path}")

    if not config.CANNONS_REPO.exists():
        print(f"Cannons repo not found at {config.CANNONS_REPO} — file written locally only, "
              f"not pushed. Set CANNONS_REPO_PATH if this should point somewhere else.")
        return
    if not push_generated_level(config.CANNONS_REPO, out_path):
        print("did not push (see reason above) — the level file is still on disk, safe to retry")


def main() -> int:
    exit_code = 0

    if len(sys.argv) > 1:
        level_number = int(sys.argv[1])
        used_passwords: set[str] = set()
        existing_signatures: set[str] = set()
    elif config.CANNONS_REPO.exists():
        level_number, used_passwords = scan_existing_levels(config.CANNONS_REPO)
        level_number += 1
        existing_signatures = scan_existing_signatures(config.CANNONS_REPO)
        print(f"scanned {config.CANNONS_REPO}: next level number = {level_number}, "
              f"{len(used_passwords)} passwords already in use, "
              f"{len(existing_signatures)} existing level shapes to avoid repeating")
    else:
        # no Cannons checkout available (e.g. running this file standalone
        # without the daily_production.yml workflow's second checkout) —
        # can't know the real next number, so don't guess with something
        # like today's date ordinal again (see level_registry.py docstring
        # for how badly that went the first time).
        print(f"Cannons repo not found at {config.CANNONS_REPO} and no level number given on the "
              f"command line — refusing to guess a level number. Pass one explicitly to test standalone.")
        return 1

    produced = 0
    try:
        policy_check = load_policy_from_file(CURRENT_POLICY_PATH)
        suite = fixed_suite() + random_suite(n=100, seed=0)
        champion_score = evaluate(policy_check, suite)
        if champion_score.win_rate < CHAMPION_WIN_RATE_THRESHOLD:
            print(f"champion policy win rate {champion_score.win_rate:.2%} is below the "
                  f"{CHAMPION_WIN_RATE_THRESHOLD:.0%} production threshold — refusing to generate "
                  f"a level today. This is a healthy no-op (learning phase still improving the "
                  f"policy), not an incident.")
        else:
            # variety level first (unchanged behavior), then the drag levels
            archetype = _pick_archetype()
            focuses = [archetype] + [DRAG_FOCUS] * DRAG_LEVELS_PER_RUN + [EXTREME_FOCUS] * EXTREME_LEVELS_PER_RUN
            for focus in focuses:
                brief = {DRAG_FOCUS: DRAG_BRIEF, EXTREME_FOCUS: EXTREME_BRIEF}.get(focus) or pacing.ARCHETYPES[focus]
                print(f"target for level {level_number}: {focus} ({brief})")
                level = generate_one_level(level_number, used_passwords, existing_signatures, focus)
                if focus in pacing.ARCHETYPES:  # the bench only tracks structural archetypes
                    _record_archetype_result(focus, produced=level is not None)
                if level is None:
                    print(f"no winnable {focus} level this run — moving on")
                    continue
                _publish_level(level, policy_check)
                # the next level in this run must not reuse this one's number/password/shape
                used_passwords.add(level.password)
                existing_signatures.add(level.shape_signature())
                level_number += 1
                produced += 1
    except PolicyLoadError as e:
        # unlike "no candidate won" below, this means production is broken,
        # not just unlucky today — worth a real red X.
        print(f"cannot load current policy: {e}")
        incident_log.record_exception("daily_generator")
        exit_code = 1
    except BudgetExceeded as e:
        print(f"budget exhausted for today ({e}) — stopping cleanly, will resume automatically.")
    except ProviderQuotaExhausted as e:
        print(f"provider rate-limited hard ({e}) — stopping cleanly, this is Groq/Anthropic's "
              f"cap, not our bug. See state/rate_limit_events.jsonl.")
    except Exception:  # noqa: BLE001 — an unexpected bug is still ours, log it and keep going to git_sync
        import traceback
        print("unexpected error generating a level, see traceback below")
        traceback.print_exc()
        incident_log.record_exception("daily_generator")
        exit_code = 1

    if produced == 0 and exit_code == 0:
        # every candidate failed the gates after MAX_ATTEMPTS retries — a real
        # (logged, audited) outcome, not a crash, so the run stays green (same
        # principle as before the multi-level change; exit_code 1 = a real bug above)
        print("no winnable candidate level today — nothing to publish, will try again next run")
    print(f"levels produced this run: {produced}")

    # every attempt (successful or not) spent real tokens and wrote audit/budget
    # state in THIS repo (CannonsLevelGen) — push that back regardless of outcome,
    # same reasoning as run_learning_cycle.py's unconditional git_sync call.
    try:
        import git_sync
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        git_sync.commit_and_push(f"[bot] daily production run - {now}")
    except Exception:  # noqa: BLE001
        import traceback
        print("git_sync: failed, see traceback below (this run's own audit/budget state may be lost)")
        traceback.print_exc()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

"""Learning loop, part B since 2026-09-24 (replaces level_designer's
free-form "rule hypotheses" in run_learning_cycle.py): the LLM helps the
regulator (verification/regulator.py) where its blind local search fell short.

One cycle = one LLM call:
  1. pick a level the regulator left unresolved, only partially fixed, or
     without its target archetype (reports/regulation/*/shard_*.json);
  2. ask the LLM to redesign it — real rules, pacing rules, the level's
     current layout and problems, the target archetype, plus its own recent
     verified successes/failures as examples (knowledge/regulation_lessons.json);
  3. verify with the SAME evaluation and fitness the regulator uses; only a
     strictly better, winnable redesign is stored, in
     reports/regulation/llm_proposals.json, which apply_regulation.py applies.

"Learning" here = the lessons file: every verified success and every
rejection reason is kept and shown to the next cycle, so the model sees what
actually works in this game instead of guessing blind each time.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

import config
from learning import knowledge
from learning.game_rules import GAME_RULES
from llm import audit, client
from policy.loader import load_policy_from_file
from sim.level import Level
from sim.real_suite import describe_level
from verification import pacing, regulator
from verification.level_mutations import normalize

REPORT_ROOT = regulator.REPORT_ROOT
PROPOSALS_PATH = REPORT_ROOT / "llm_proposals.json"
LESSONS_PATH = config.KNOWLEDGE_DIR / "regulation_lessons.json"
STATE_PATH = config.STATE_DIR / "regulation_designer.json"
LESSONS_IN_PROMPT = 3
RETRY_AFTER_ATTEMPTS = 2  # a level the LLM failed this many times is skipped

_SCHEMA = """\
Respond with a one-sentence design note, then ONLY this JSON in a ```json block:
{"filas": [{"cuadros": [{"index": <0-4>, "tipo": <1-3>, "hp": <1-10>}, ...]}, ...]}
index = column (0=right .. 4=left), one pirate per column per fila, every fila
has at least one pirate. (The last-pirate skin is set automatically.)"""


def _load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _candidates() -> list[dict]:
    """Regulator records worth a second opinion, most-needed first."""
    records = []
    for path in sorted(REPORT_ROOT.glob("*/shard_*.json")):
        records += json.loads(path.read_text(encoding="utf-8"))["levels"]
    # unresolved_skill (verification/skill_pass.py, 2026-10-07) go first: they
    # are release levels failing their role (peak/late body a naive player
    # wins, repetitive arc, dull breather) that the local search couldn't fix
    order = {"unresolved_skill": -1, "unresolved": 0, "repaired_partial": 1, "improved_partial": 2}
    out = []
    for r in records:
        if "level" not in r or "after" not in r:
            continue
        if r["status"] == "unresolved_skill":
            out.append((-1, r["levelNumber"], r))
            continue
        missed_archetype = r.get("target_archetype") and r["after"].get("primary") != r["target_archetype"]
        rank = order.get(r["status"], 3 if missed_archetype else None)
        # tank targets last (2026-09-26): 0 real tanks from any LLM attempt so
        # far (policy can't plan merges; see daily_generator's archetype bench),
        # and they were 71 of the 159 open candidates — do the others first.
        if rank == 3 and r.get("target_archetype") == "tank":
            rank = 4
        if rank is not None:
            out.append((rank, r["levelNumber"], r))
    return [r for _, _, r in sorted(out, key=lambda x: (x[0], x[1]))]


def _pick(records: list[dict], state: dict, proposals: dict) -> dict | None:
    for r in records:
        n = str(r["levelNumber"])
        if _is_skill(r):
            # separate attempt counter: older regulator attempts on the same
            # level were about a different goal; an older applied proposal is
            # already in the asset the skill goal starts from
            done = proposals.get(n, {}).get("skill_goal") is not None
            if done or state.get("attempts", {}).get(f"skill:{n}", 0) >= RETRY_AFTER_ATTEMPTS:
                continue
            return r
        if n in proposals or state.get("attempts", {}).get(n, 0) >= RETRY_AFTER_ATTEMPTS:
            continue
        return r
    return None


def _is_skill(record: dict) -> bool:
    return record.get("status") == "unresolved_skill"


_SKILL_GOAL_TEXT = {
    "harden": ("it is too easy for its place in the campaign: a NAIVE player (drops each new cannon on the "
               "most threatened column, merges only onto an undersized cannon, NEVER moves a placed cannon) "
               "wins it. Make that naive player LOSE while a good player still wins: pressure that switches "
               "sides so cannons must be moved, a pirate blocked behind another in its column, or a tall "
               "pirate whose merge must be prepared rounds earlier. Keep about the same number of pirates."),
    "reshape": ("its arc repeats one design idea too often. Rebuild it around the TARGET ARCHETYPE below, "
                "no easier than now (if the naive player loses it now, it must still lose)."),
    "breather": ("it is a BREATHER (relief level after a hard peak) but dull: a line of lone HP-1 pirates "
                 "identical to other breathers. Make it short (4-6 rounds), easy (a naive player still wins), "
                 "with 2+ pirates in most rounds, HP at most {max_hp}, one pirate getting past the first row, "
                 "built around the TARGET ARCHETYPE below."),
}


def _lessons_block(lessons: list[dict]) -> str:
    if not lessons:
        return "(no lessons yet)"
    lines = []
    for lesson in lessons[-LESSONS_IN_PROMPT:]:
        verdict = "WORKED" if lesson["success"] else f"FAILED ({lesson['reason']})"
        lines.append(f"- target {lesson['target']}: {lesson['note']} -> {verdict}")
    return "\n".join(lines)


def _build_skill_prompt(record: dict, lessons: list[dict]) -> tuple[str, str]:
    """Prompt for a skill_pass goal; starts from the level as it is in the game."""
    goal = record["goal"]
    level = Level.from_dict(record["original"])
    target = goal.get("target_archetype")
    system = ("You redesign one level of the tower-defense game Cannons so it does its job in the "
              "campaign. Use only the real rules below.")
    user = f"""{GAME_RULES}

PACING RULES (checked automatically):
- the player gains one cannon per round, so later rounds must carry MORE danger;
- at most {pacing.MAX_SINGLE_PIRATE_SHARE:.0%} single-pirate rounds; at most {pacing.MAX_FILAS} rounds;
- prefer several pirates across columns over one very tall pirate.

LEVEL TO REDESIGN — {_SKILL_GOAL_TEXT[goal['kind']].format(max_hp=goal.get('max_hp', 3))}
(why it was flagged: {goal.get('reason', '')})
{describe_level(level)}

TARGET ARCHETYPE: {f"{target} — {pacing.ARCHETYPES[target]}" if target else "keep its current idea"}

Your recent attempts (learn from them):
{_lessons_block(lessons)}

{_SCHEMA}"""
    return system, user


def _build_prompt(record: dict, lessons: list[dict]) -> tuple[str, str]:
    if _is_skill(record):
        return _build_skill_prompt(record, lessons)
    level = Level.from_dict(record["level"])
    after = record["after"]
    problems = after["pacing"]["problems"] or ["none"]
    status = "NOT winnable yet" if after["classification"] == "no_win_found" else "winnable"
    target = record.get("target_archetype") or "any"
    system = ("You redesign one level of the tower-defense game Cannons so it is winnable, tense "
              "until the end, and clearly shows one design idea. Use only the real rules below.")
    user = f"""{GAME_RULES}

PACING RULES (checked automatically):
- the player gains one cannon per round, so later rounds must carry MORE danger;
- at most {pacing.MAX_SINGLE_PIRATE_SHARE:.0%} single-pirate rounds; at most {pacing.MAX_FILAS} rounds;
- prefer several pirates across columns over one very tall pirate.

LEVEL TO REDESIGN (currently {status}; problems: {'; '.join(problems)}):
{describe_level(level)}

TARGET ARCHETYPE: {target} — {pacing.ARCHETYPES.get(target, 'any clear idea')}

Your recent attempts (learn from them):
{_lessons_block(lessons)}

Keep roughly the same length and difficulty; change what's needed.
{_SCHEMA}"""
    return system, user


def _extract_level(text: str) -> Level | None:
    match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return Level.from_dict({"levelNumber": 0, "password": "", "isHard": False, "filas": data["filas"]})
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _record_lesson(lessons: list[dict], target: str, note: str, success: bool, reason: str = "") -> None:
    lessons.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "target": target, "note": note[:200], "success": success, "reason": reason})
    LESSONS_PATH.write_text(json.dumps(lessons[-200:], indent=1, ensure_ascii=False), encoding="utf-8")


def run_cycle() -> dict:
    proposals = _load_json(PROPOSALS_PATH, {})
    state = _load_json(STATE_PATH, {"attempts": {}})
    record = _pick(_candidates(), state, proposals)
    if record is None:
        return {"skipped": "no regulator results needing help (yet)"}

    lessons = _load_json(LESSONS_PATH, [])
    number = str(record["levelNumber"])
    skill = _is_skill(record)
    if skill:
        target = f"{record['goal']['kind']}:{record['goal'].get('target_archetype') or 'any'}"
    else:
        target = record.get("target_archetype") or "any"
    system, user = _build_prompt(record, lessons)
    completion = client.complete(system, user, max_tokens=3000,
                                 reserve_tokens=config.DAILY_PRODUCTION_RESERVE_TOKENS,
                                 reasoning_effort="low")
    note = completion.text.split("```")[0].strip()
    key = f"skill:{number}" if skill else number
    state["attempts"][key] = state["attempts"].get(key, 0) + 1
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

    candidate = _extract_level(completion.text)
    outcome = _evaluate_skill(record, candidate) if skill else _evaluate(record, candidate, target)
    _record_lesson(lessons, target, note, outcome["accepted"], outcome.get("reason", ""))
    if outcome["accepted"]:
        proposals[number] = outcome.pop("proposal")
        PROPOSALS_PATH.write_text(json.dumps(proposals, indent=1, ensure_ascii=False), encoding="utf-8")
    knowledge.append_log(f"regulation_designer: level {number}",
                         f"{'accepted' if outcome['accepted'] else 'rejected'} — {outcome.get('reason', note)[:300]}")
    audit.record_call(caller="regulation_designer", completion=completion, system=system, user=user,
                      outcome={"level": record["levelNumber"], **outcome})
    return {"level": record["levelNumber"], **outcome}


def _naive_won(level: Level) -> bool:
    """Does the naive baseline (never moves a cannon) win this level?"""
    from policy.baseline import BaselinePolicy
    from sim.engine import run_level
    return run_level(level, BaselinePolicy()).won


def _evaluate_skill(record: dict, candidate: Level | None) -> dict:
    """Skill goal: accepted only if it fully meets the goal, judged by the same
    penalty the local skill pass uses (0 = met)."""
    from verification import skill_pass  # local import: only needed for skill records

    if candidate is None:
        return {"accepted": False, "reason": "no valid JSON level"}
    errors = candidate.structure_errors()
    if errors:
        return {"accepted": False, "reason": f"out of range: {errors[:2]}"}
    candidate = normalize(candidate)
    original = Level.from_dict(record["original"])
    candidate.levelNumber, candidate.password, candidate.isHard = original.levelNumber, original.password, original.isHard
    goal = skill_pass.Goal(**record["goal"])
    evaluate = skill_pass.Evaluator()
    before, after = evaluate(normalize(original), goal.is_peak), evaluate(candidate, goal.is_peak)
    # learning.yml doesn't check out the Cannons repo: shapes come from the
    # snapshot skill_pass writes next to its results
    taken = set(_load_json(skill_pass.SIGNATURES_PATH, [])) - {original.shape_signature()}
    left = skill_pass.penalty(goal, after, before, taken, candidate.shape_signature())
    if left > 0:
        why = []
        if not after.winnable or (not goal.is_peak and not after.champion_won):
            why.append("not winnable by the trained AI")
        if goal.kind == "breather" and not after.naive_won:
            why.append("too hard for a breather (the naive player loses)")
        if goal.kind != "breather" and after.naive_won and (goal.kind == "harden" or goal.keep_naive_lost):
            why.append("the naive player still wins")
        if goal.target_archetype and after.primary != goal.target_archetype:
            why.append(f"archetype {after.primary} != {goal.target_archetype}")
        if after.pacing["problems"] and goal.kind != "breather":
            why.append("pacing: " + "; ".join(after.pacing["problems"]))
        return {"accepted": False, "reason": f"goal not met ({', '.join(why) or f'penalty {left:.0f}'})"}
    return {"accepted": True, "reason": f"{goal.kind} goal met, archetype {after.primary}, naive_won {after.naive_won}",
            "proposal": {"level": candidate.to_dict(), "after": asdict(after), "skill_goal": goal.kind}}


def _evaluate(record: dict, candidate: Level | None, target: str) -> dict:
    """Same evaluation + fitness as the regulator; accepted only if strictly better."""
    if candidate is None:
        return {"accepted": False, "reason": "no valid JSON level"}
    errors = candidate.structure_errors()
    if errors:
        return {"accepted": False, "reason": f"out of range: {errors[:2]}"}
    candidate = normalize(candidate)
    original = Level.from_dict(record["level"])
    candidate.levelNumber, candidate.password, candidate.isHard = original.levelNumber, original.password, original.isHard

    evaluate = regulator.Evaluator(load_policy_from_file(config.ROOT / "policy" / "current.py"))
    before, after = evaluate(original), evaluate(candidate)
    target_difficulty = (record["before"]["difficulty"] if record["before"]["classification"] != "no_win_found"
                         else regulator.REPAIR_TARGET_DIFFICULTY)
    archetype = None if target == "any" else target
    old_fit = regulator.fitness(before, target_difficulty, archetype)
    new_fit = regulator.fitness(after, target_difficulty, archetype)
    if not after.winnable:
        return {"accepted": False, "reason": f"not winnable ({after.pirates_left} pirates survive the solver)"}
    # Pacing guard (2026-09-26): fitness ranks "winnable" above everything and
    # penalizes length hard, so gutted levels scored as improvements — 257
    # (broken) became 8 lone pirates in one column, 508 (18 tense rounds)
    # became 10 HP-1 pirates. A broken level must come back fully paced; a
    # winnable one may not gain pacing problems.
    old_problems, new_problems = before.pacing["problems"], after.pacing["problems"]
    if not before.winnable and new_problems:
        return {"accepted": False, "reason": f"repaired but fails pacing ({'; '.join(new_problems)})"}
    if before.winnable and len(new_problems) > len(old_problems):
        return {"accepted": False, "reason": f"more pacing problems than the current version "
                                             f"({len(old_problems)} -> {len(new_problems)}: {'; '.join(new_problems)})"}
    # Skill guard (2026-10-07): 5 of the last 6 accepted proposals turned a
    # level the naive player (policy/baseline.py, never moves cannons) loses
    # into one it wins — "better" by fitness, but easier and duller.
    if before.winnable and not _naive_won(original) and _naive_won(candidate):
        return {"accepted": False, "reason": "easier than the current version: a player who never moves "
                                             "a cannon now wins it"}
    if new_fit <= old_fit:
        problems = "; ".join(after.pacing["problems"]) or f"archetype {after.primary} != {target}"
        return {"accepted": False, "reason": f"not better than the regulator's version ({problems})"}
    return {"accepted": True, "reason": f"fitness {old_fit:.0f} -> {new_fit:.0f}, archetype {after.primary}",
            "proposal": {"level": candidate.to_dict(), "after": asdict(after), "fitness": new_fit,
                         "improves_on_fitness": old_fit}}

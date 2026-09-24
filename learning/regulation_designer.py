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
    order = {"unresolved": 0, "repaired_partial": 1, "improved_partial": 2}
    out = []
    for r in records:
        if "level" not in r or "after" not in r:
            continue
        missed_archetype = r.get("target_archetype") and r["after"].get("primary") != r["target_archetype"]
        rank = order.get(r["status"], 3 if missed_archetype else None)
        if rank is not None:
            out.append((rank, r["levelNumber"], r))
    return [r for _, _, r in sorted(out, key=lambda x: (x[0], x[1]))]


def _pick(records: list[dict], state: dict, proposals: dict) -> dict | None:
    for r in records:
        n = str(r["levelNumber"])
        if n in proposals or state.get("attempts", {}).get(n, 0) >= RETRY_AFTER_ATTEMPTS:
            continue
        return r
    return None


def _lessons_block(lessons: list[dict]) -> str:
    if not lessons:
        return "(no lessons yet)"
    lines = []
    for lesson in lessons[-LESSONS_IN_PROMPT:]:
        verdict = "WORKED" if lesson["success"] else f"FAILED ({lesson['reason']})"
        lines.append(f"- target {lesson['target']}: {lesson['note']} -> {verdict}")
    return "\n".join(lines)


def _build_prompt(record: dict, lessons: list[dict]) -> tuple[str, str]:
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
    target = record.get("target_archetype") or "any"
    system, user = _build_prompt(record, lessons)
    completion = client.complete(system, user, max_tokens=3000,
                                 reserve_tokens=config.DAILY_PRODUCTION_RESERVE_TOKENS,
                                 reasoning_effort="low")
    note = completion.text.split("```")[0].strip()
    state["attempts"][number] = state["attempts"].get(number, 0) + 1
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

    outcome = _evaluate(record, _extract_level(completion.text), target)
    _record_lesson(lessons, target, note, outcome["accepted"], outcome.get("reason", ""))
    if outcome["accepted"]:
        proposals[number] = outcome.pop("proposal")
        PROPOSALS_PATH.write_text(json.dumps(proposals, indent=1, ensure_ascii=False), encoding="utf-8")
    knowledge.append_log(f"regulation_designer: level {number}",
                         f"{'accepted' if outcome['accepted'] else 'rejected'} — {outcome.get('reason', note)[:300]}")
    audit.record_call(caller="regulation_designer", completion=completion, system=system, user=user,
                      outcome={"level": record["levelNumber"], **outcome})
    return {"level": record["levelNumber"], **outcome}


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
    if new_fit <= old_fit:
        problems = "; ".join(after.pacing["problems"]) or f"archetype {after.primary} != {target}"
        return {"accepted": False, "reason": f"not better than the regulator's version ({problems})"}
    return {"accepted": True, "reason": f"fitness {old_fit:.0f} -> {new_fit:.0f}, archetype {after.primary}",
            "proposal": {"level": candidate.to_dict(), "after": asdict(after), "fitness": new_fit,
                         "improves_on_fitness": old_fit}}

"""Month-1 loop, part B: the AI proposes level-design hypotheses and tests
them empirically against its OWN current best policy (not the human-authored
rules in Cannons/CLAUDE.md or project memory — those are seed hypotheses to
test and potentially overturn, not ground truth).

Example of the kind of thing this loop is meant to discover on its own (from
the user's own brief): "a pirate with HP 3+ appearing after round 3 might
still be winnable, because cannons will already have merged by then" —
that's exactly a hypothesis + a level to test it against, which is what each
cycle here produces.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import config
from llm import client, audit
from learning import knowledge
from learning.game_rules import GAME_RULES
from policy.loader import load_policy_from_file, PolicyLoadError
from sim.engine import run_level
from sim.level import Level, pad_leading, pad_trailing

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"

# How many extra empty filas the robustness sweep pads on each side of the
# model's own level before re-playing it. See run_cycle()'s comment for why.
_ROBUSTNESS_PAD = 5

_LEVEL_SCHEMA = """\
Response JSON schema (must match exactly):
{
  "predicted_outcome": "winnable" | "unwinnable",
  "levelNumber": <int>,
  "password": "<1 uppercase letter + 4 digits, e.g. Q1234>",
  "isHard": <bool>,
  "filas": [
    {"cuadros": [{"index": <0-4>, "tipo": <1-4>, "hp": <1-10>}, ...]},
    ...
  ]
}
"predicted_outcome" is your claim about this level's own result, checked
against the real simulation before your rule is trusted — must agree with
your hypothesis text.
Each "fila" is one wave/round. "cuadros" may be an empty list (a breathing-room
round with no pirates). "index" is the column (0=right .. 4=left). "tipo" 1-3
are visually-different normal pirates (same difficulty), 4 marks the last
pirate of the level (use it exactly once, on the final fila that has any
pirates). Max 5 cuadros per fila (one per column, no two pirates share a
column+round).
"""


def _build_prompt() -> tuple[str, str]:
    system = (
        "You design levels for a small tower-defense game (Cannons) and test your "
        "own design hypotheses by simulation before trusting them. Be specific and "
        "falsifiable: state a hypothesis, then build ONE level designed to be a "
        "sharp test of it (not a generic level). Ground hypotheses in the real "
        "rules below — never invent a mechanic that isn't stated there, no matter "
        "how plausible it sounds."
    )
    user = f"""{GAME_RULES}

{_LEVEL_SCHEMA}

Confirmed rules so far from previous cycles:
{knowledge.rules_as_prompt_block()}

Seed hypotheses worth stress-testing (human-authored guesses, not verified by
your own play — confirm, refine, or overturn them):
- With cannon base damage 1, a lone pirate needs HP <= 3 to be beatable without
  any merge.
- Blocking has nothing to do with row distance: the most-advanced pirate in a
  column always absorbs the shot, no matter how far behind the next one is —
  a gap only buys the pirate behind extra rounds once the one in front dies,
  it never lets both take damage in the same round (see GAME_RULES above).
- Merge budget roughly scales with number of rows minus ~5.

Propose ONE hypothesis (1-2 sentences) about what makes a level winnable or fun
that isn't already confirmed above, then ONE level (JSON) built specifically to
test it sharply. Respond with the hypothesis as plain text, then the JSON in a
single ```json code block.

Your level gets auto-retested with extra empty filas added before and after
it — if that changes the result, your rule gets discarded regardless of this
run. Avoid hypotheses that depend on total filas count or on timing relative
to the level's start/end.
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


def run_cycle() -> dict:
    """Runs exactly one propose -> simulate -> record cycle. Raises
    llm.budget.BudgetExceeded if the daily cap is spent."""
    system, user = _build_prompt()
    # 7000 -> 3000 (2026-08-31) -> 5000 (2026-09-01). The 3000 ceiling killed
    # the 413s but audited raw responses (audit/2026-08-30/31 + 09-01) show
    # ~half of "no_json" failures aren't empty — they're valid JSON cut off
    # mid-structure, with tokens_used pinned at the prompt+max_tokens ceiling
    # every time (reasoning + hypothesis text + a multi-fila level often
    # doesn't fit in 3000). strategy_learner runs with a BIGGER prompt
    # (~3.7-3.9k tokens, includes the full policy source) at max_tokens=3000
    # and sees zero 413s in production — so ~6200-6900 total tokens is a
    # proven-safe zone on this tier. level_designer's own prompt is only
    # ~1.7-2k tokens, so max_tokens=5000 lands it in that same proven-safe
    # zone (~6700-7000 total) while giving ~67% more room to finish the JSON.
    completion = client.complete(system, user, max_tokens=5000)
    response = completion.text

    hypothesis = response.split("```")[0].strip()
    level_dict = _extract_json(response)
    if level_dict is None:
        knowledge.append_log("level_designer: rejected", "No valid JSON level block in response.")
        outcome = {"recorded": False, "reason": "no_json"}
        audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    predicted_outcome = level_dict.get("predicted_outcome")
    if predicted_outcome not in ("winnable", "unwinnable"):
        knowledge.append_log(
            "level_designer: rejected",
            f"Missing or invalid predicted_outcome: {predicted_outcome!r}",
        )
        outcome = {"recorded": False, "reason": "missing_prediction"}
        audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    try:
        level = Level.from_dict(level_dict)
    except (KeyError, TypeError) as e:
        knowledge.append_log("level_designer: rejected", f"Level JSON didn't match schema: {e}")
        outcome = {"recorded": False, "reason": f"schema_error: {e}"}
        audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    try:
        policy = load_policy_from_file(CURRENT_POLICY_PATH)
    except PolicyLoadError as e:
        knowledge.append_log("level_designer: error", f"Could not load current policy: {e}")
        outcome = {"recorded": False, "reason": f"policy_load_error: {e}"}
        audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    engine = run_level(level, policy)

    evidence = (
        f"level '{level.password}' ({level.total_pirates()} pirates, {len(level.filas)} filas, "
        f"max_hp={level.max_hp()}, columns={sorted(level.active_columns())}) "
        f"played with current policy ({getattr(policy, 'name', 'unnamed')}): "
        f"{'WON' if engine.won else 'LOST'} in {engine.rounds_played} rounds"
    )

    # Check 1: does the model's own explicit prediction match what actually
    # happened? A "confirmed" rule whose hypothesis predicted the opposite of
    # the real result isn't confirming anything — it's self-contradictory.
    # Added 2026-09-01 after finding exactly this in the knowledge base twice
    # (a hypothesis text predicting "winnable" stored alongside a level that
    # LOST, and vice versa) - see cannonslevelgen audit that day.
    predicted_won = predicted_outcome == "winnable"
    if predicted_won != engine.won:
        knowledge.append_log(
            "level_designer: refuted",
            f"Hypothesis: {hypothesis}\n\nPredicted {predicted_outcome}, but "
            f"the level actually {'WON' if engine.won else 'LOST'}. {evidence}",
        )
        outcome = {"recorded": False, "reason": "predicted_mismatch", "hypothesis": hypothesis}
        audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    # Check 2: does the SAME claim still hold if this exact scenario happens
    # earlier or later in a longer level? Added 2026-09-01 after finding that
    # most purged rules were really just "true for this one filas count" -
    # the real game has no such dependency (a pirate's fate is fixed by its
    # own HP/position/blocking, never by how many filas the level has before
    # or after it - see game_rules.py). Free to check: pure local simulation,
    # no extra LLM tokens spent.
    variants = {
        "padded before": pad_leading(level, _ROBUSTNESS_PAD),
        "padded after": pad_trailing(level, _ROBUSTNESS_PAD),
    }
    for label, variant in variants.items():
        variant_engine = run_level(variant, policy)
        if variant_engine.won != engine.won:
            knowledge.append_log(
                "level_designer: parameter-sensitive",
                f"Hypothesis: {hypothesis}\n\nBase level {'WON' if engine.won else 'LOST'}, "
                f"but flipped to {'WON' if variant_engine.won else 'LOST'} when {label} with "
                f"{_ROBUSTNESS_PAD} empty filas - not a real rule, just this level's specific "
                f"timing. {evidence}",
            )
            outcome = {"recorded": False, "reason": "parameter_sensitive", "hypothesis": hypothesis}
            audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
            return outcome

    knowledge.add_rule(knowledge.LearnedRule(
        date=datetime.now().date().isoformat(),
        statement=f"{hypothesis} => {'confirmed winnable' if engine.won else 'confirmed NOT winnable'} by test level.",
        evidence=f"{evidence} (robustness: consistent when padded with {_ROBUSTNESS_PAD} empty filas on either side)",
        confidence=0.7,  # bumped from 0.5 now that predicted-outcome + robustness sweep both passed
        source="level_designer",
    ))
    knowledge.append_log("level_designer", f"Hypothesis: {hypothesis}\n\nEvidence: {evidence}")

    outcome = {"recorded": True, "won": engine.won, "hypothesis": hypothesis, "level_password": level.password}
    audit.record_call(caller="level_designer", completion=completion, system=system, user=user, outcome=outcome)
    return outcome

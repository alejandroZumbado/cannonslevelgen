"""Month-1 loop, part A: the AI rewrites its own cannon-placement policy.

One call to run_cycle() = one LLM call proposing a full replacement for
policy/current.py, benchmarked for free (pure Python, thousands of rounds)
against sim/benchmark.py, promoted only if it strictly beats the current
policy's score. This is what lets a whole month of exploration fit inside the
free-tier daily token budget: the expensive part (the LLM call) happens once
per cycle, not once per in-game move.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

import config
from llm import client, budget, audit
from learning import knowledge
from policy.loader import load_policy_from_file, load_policy_from_source, PolicyLoadError
from sim.benchmark import fixed_suite, random_suite
from sim.evaluate import evaluate

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"

_ENGINE_SPEC = """\
Interface you can rely on (do not invent other attributes/methods):

engine.cannons: dict[int, Cannon]   # key = column 0-4, only columns with a placed cannon
  Cannon.column: int
  Cannon.damage: int                 # 1 = base, higher = merged

engine.pirates: list[Pirate]         # all pirates currently alive on the board
  Pirate.column: int                 # 0-4
  Pirate.position: int                # 0, 1, or 2 (valid); a pirate that would reach
                                       # position 3 ends the game in a loss
  Pirate.hp: int
  Pirate.tipo: int

engine.round: int                     # current round number
engine.rounds_played: int

Your choose_action(self, engine) must return exactly one of:
  ("spawn", col)          # places the pending new cannon (damage 1) at column col (0-4);
                            # if col already has a cannon, this MERGES (damage stacks)
  ("move", from_col, to_col)  # relocates an already-placed cannon; if to_col is
                                # occupied this also merges. Sacrifices that round's
                                # new spawn cannon (it stays pending for next round).

Only cannons that exist deal damage; only the MOST ADVANCED pirate in a column
(highest `position`) gets hit by that column's cannon each round — this is a
fixed game rule, not something you control.
"""


def _extract_code(text: str) -> str | None:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None


def _build_prompt(current_source: str, current_score, rng_seed: int) -> tuple[str, str]:
    system = (
        "You are iterating on a game-playing policy for a small tower-defense game "
        "called Cannons. You write plain Python, no imports, no I/O. Your goal is a "
        "policy that wins as many levels as possible. You have full freedom to "
        "redesign the strategy, including using lookahead/simulation over the action "
        "list if useful — engine objects are plain data, cheap to reason about."
    )
    user = f"""{_ENGINE_SPEC}

Current policy source (win rate {current_score.win_rate:.2%} over {current_score.total} test levels):

```python
{current_source}
```

Learned rules so far (from independent play-testing, may be useful context):
{knowledge.rules_as_prompt_block()}

Propose an improved full replacement for this policy. Requirements:
- Must define `class Policy` with method `choose_action(self, engine)`.
- No imports, no file/network access, no infinite loops.
- Keep it deterministic (no randomness) so benchmarking is reproducible.

Respond with a short paragraph (2-3 sentences) explaining the change and why you
think it helps, THEN the full new policy source in a single ```python code block.
Random seed for context (irrelevant to your answer, just varies the prompt): {rng_seed}
"""
    return system, user


def run_cycle() -> dict:
    """Runs exactly one propose -> benchmark -> promote-or-discard cycle.
    Returns a small dict summary for the caller to print/log. Raises
    llm.budget.BudgetExceeded if the daily cap is already spent — callers
    should catch that and stop, not treat it as an error."""
    current_source = CURRENT_POLICY_PATH.read_text(encoding="utf-8")
    current_policy = load_policy_from_file(CURRENT_POLICY_PATH)

    suite = fixed_suite() + random_suite(n=25, seed=datetime.now().microsecond)
    current_score = evaluate(current_policy, suite)

    system, user = _build_prompt(current_source, current_score, rng_seed=datetime.now().microsecond)
    completion = client.complete(system, user, max_tokens=4000)
    response = completion.text

    code = _extract_code(response)
    if code is None:
        knowledge.append_log("strategy_learner: rejected", "LLM response had no python code block.")
        outcome = {"promoted": False, "reason": "no_code_block"}
        audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    try:
        candidate_policy = load_policy_from_source(code)
    except PolicyLoadError as e:
        knowledge.append_log("strategy_learner: rejected", f"Candidate failed to load: {e}")
        outcome = {"promoted": False, "reason": f"load_error: {e}"}
        audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    candidate_score = evaluate(candidate_policy, suite)

    reasoning = response.split("```")[0].strip()

    if candidate_score.better_than(current_score):
        _archive_current(current_source)
        CURRENT_POLICY_PATH.write_text(code, encoding="utf-8")
        knowledge.append_log(
            "strategy_learner: PROMOTED",
            f"win rate {current_score.win_rate:.2%} -> {candidate_score.win_rate:.2%} "
            f"(avg rounds {current_score.avg_rounds_played:.1f} -> {candidate_score.avg_rounds_played:.1f})\n\n"
            f"Reasoning given: {reasoning}",
        )
        outcome = {
            "promoted": True,
            "old_win_rate": current_score.win_rate,
            "new_win_rate": candidate_score.win_rate,
        }
        audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    knowledge.append_log(
        "strategy_learner: rejected (no improvement)",
        f"candidate win rate {candidate_score.win_rate:.2%} vs current {current_score.win_rate:.2%}\n\n"
        f"Reasoning given: {reasoning}",
    )
    outcome = {
        "promoted": False,
        "reason": "not_better",
        "old_win_rate": current_score.win_rate,
        "candidate_win_rate": candidate_score.win_rate,
    }
    audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
    return outcome


def _archive_current(source: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (config.POLICY_HISTORY_DIR / f"{stamp}.py").write_text(source, encoding="utf-8")

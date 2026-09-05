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
from learning import knowledge, strategy_history
from learning.game_rules import GAME_RULES
from policy.loader import load_policy_from_file, load_policy_from_source, PolicyLoadError
from sim.benchmark import fixed_suite, random_suite
from sim.evaluate import evaluate

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"

# Cycles in a row without a promotion before switching from "propose a full
# rewrite" to "propose one small targeted fix to the current champion". Added
# 2026-08-24 after the rewrite-only framing went 10 straight cycles rejected
# (only 1 promotion ever, 76%->79%, in the whole history so far) — a full
# redesign every time lets the LLM bounce between different-but-equally-
# mediocre approaches instead of actually improving on the best one found.
_REFINE_MODE_STREAK_THRESHOLD = 3

# 25 random + 4 fixed (29 total) gave noisy win-rate deltas of several
# percentage points between otherwise-identical policies just from which
# random levels got drawn (verified empirically 2026-08-24) — enough to
# plausibly reject a genuinely-better candidate as "not better". Bumped to
# 100 random + 4 fixed; simulation is pure Python with no LLM cost, so this
# is free (measured <20ms for the whole suite even at 150).
_RANDOM_SUITE_SIZE = 100

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
  Pirate.tipo: int                    # COSMETIC ONLY — see GAME RULES below.
                                       # Never affects damage/blocking/targeting.

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


def _build_prompt(current_source: str, current_score, rng_seed: int, *,
                   refine_mode: bool, recent_attempts: str) -> tuple[str, str]:
    recent_block = (
        f"\nRecently rejected attempts — do NOT propose something that amounts to the "
        f"same idea again, they already lost to the current champion:\n{recent_attempts}\n"
        if recent_attempts else ""
    )

    if refine_mode:
        system = (
            "You are iterating on a game-playing policy for a small tower-defense game "
            "called Cannons. You write plain Python, no imports, no I/O. The last several "
            "full-rewrite proposals all failed to beat the current champion policy — stop "
            "redesigning from scratch and instead make ONE small, targeted change to the "
            "champion below that fixes a specific weakness you can identify. Base that "
            "weakness on the real rules given below — never invent a mechanic (e.g. tying "
            "behavior to `tipo`) that isn't stated there."
        )
        task = (
            "The current policy is a proven champion — full rewrites keep losing to it. "
            "Propose ONE small, targeted change to THIS EXACT policy (not a redesign): "
            "adjust one threshold, add one new condition/branch, reorder one priority, or "
            "fix one specific case you can point to. Keep everything else identical. "
            "State in 1-2 sentences exactly which weakness of the current policy (ideally "
            "referencing a concrete column/HP/round scenario) your change addresses."
        )
    else:
        system = (
            "You are iterating on a game-playing policy for a small tower-defense game "
            "called Cannons. You write plain Python, no imports, no I/O. Your goal is a "
            "policy that wins as many levels as possible. You have full freedom to "
            "redesign the strategy, including using lookahead/simulation over the action "
            "list if useful — engine objects are plain data, cheap to reason about."
        )
        task = (
            "Propose an improved full replacement for this policy. Explain in 2-3 sentences "
            "what idea you're trying that's genuinely different from the recently rejected "
            "attempts above, not a small variation on the same theme."
        )

    user = f"""{GAME_RULES}

{_ENGINE_SPEC}

Current policy source (win rate {current_score.win_rate:.2%} over {current_score.total} test levels):

```python
{current_source}
```

Learned rules so far (from independent play-testing, may be useful context):
{knowledge.rules_as_prompt_block()}
{recent_block}
{task} Requirements:
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

    suite = fixed_suite() + random_suite(n=_RANDOM_SUITE_SIZE, seed=datetime.now().microsecond)
    current_score = evaluate(current_policy, suite)

    streak = strategy_history.consecutive_rejections()
    refine_mode = streak >= _REFINE_MODE_STREAK_THRESHOLD
    # n=4 -> 10 on 2026-09-01: knowledge/strategy_history.json (20-entry rolling
    # window) shows the same 2-3 ideas ("prefer move over spawn on ties",
    # "extend second-round lookahead to include moves") getting rejected over
    # and over across the full window (7/20 and 9/20 hits respectively) - the
    # old n=4 view was too narrow to stop them resurfacing a few cycles later,
    # wasting real Groq budget re-testing already-dead ideas. Measured locally:
    # n=10 adds ~850 chars (~210 tokens) to the prompt, landing total request
    # tokens at ~6680 - still inside the zone this caller already runs safely
    # in today (~6470 at n=4), well under the known 413 threshold (~8450).
    recent_attempts = strategy_history.recent_attempts_block(10)

    system, user = _build_prompt(current_source, current_score, rng_seed=datetime.now().microsecond,
                                  refine_mode=refine_mode, recent_attempts=recent_attempts)
    # 4000 -> 3000 on 2026-08-28: prompt (policy source + GAME_RULES + learned
    # rules + recent attempts) measured ~4450 tokens; with max_tokens=4000 the
    # combined request+completion budget (~8450) tripped Groq's 413 on every
    # single cycle from 2026-08-26T19:17 onward (see knowledge.py's
    # _MAX_RULES_IN_PROMPT comment for the full incident). 3000 is the documented
    # floor for openai/gpt-oss-120b (client.py) — do not go lower without
    # re-verifying the empty-response gotcha.
    #
    # 3000 -> 3400 on 2026-09-05: the prompt grew since 08-28 (recent_attempts
    # window 4->10 on 09-01, policy/knowledge both grew) to ~4730-4820 tokens
    # measured locally (_build_prompt with the real current policy, both
    # refine_mode values). audit/2026-09-05.jsonl showed 4 of 5 calls that day
    # failing with reason "no_code_block" at tokens_used ~7550-7650 — the
    # model was spending its *entire* 3000-token completion budget on internal
    # reasoning and getting cut off mid code-block (same gotcha as
    # level_designer's old truncated-JSON bug, just manifesting here now).
    # 3400 puts the worst-case total at ~8220, still ~230 tokens under the
    # measured 413 ceiling (~8450) — a real but deliberately thin margin since
    # the prompt keeps growing over time. If 413s reappear for this caller,
    # the fix is trimming the prompt (e.g. the recent-attempts window), not
    # pushing max_tokens further — this margin has no more room to give.
    completion = client.complete(system, user, max_tokens=3400)
    response = completion.text

    code = _extract_code(response)
    if code is None:
        knowledge.append_log("strategy_learner: rejected", "LLM response had no python code block.")
        strategy_history.record(promoted=False, win_rate=current_score.win_rate, candidate_win_rate=0.0,
                                 summary="no python code block in response")
        outcome = {"promoted": False, "reason": "no_code_block"}
        audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    try:
        candidate_policy = load_policy_from_source(code)
    except PolicyLoadError as e:
        knowledge.append_log("strategy_learner: rejected", f"Candidate failed to load: {e}")
        strategy_history.record(promoted=False, win_rate=current_score.win_rate, candidate_win_rate=0.0,
                                 summary=f"candidate failed to load: {e}")
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
        strategy_history.record(promoted=True, win_rate=current_score.win_rate,
                                 candidate_win_rate=candidate_score.win_rate, summary=reasoning)
        outcome = {
            "promoted": True,
            "old_win_rate": current_score.win_rate,
            "new_win_rate": candidate_score.win_rate,
            "refine_mode": refine_mode,
        }
        audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
        return outcome

    knowledge.append_log(
        "strategy_learner: rejected (no improvement)",
        f"candidate win rate {candidate_score.win_rate:.2%} vs current {current_score.win_rate:.2%}\n\n"
        f"Reasoning given: {reasoning}",
    )
    strategy_history.record(promoted=False, win_rate=current_score.win_rate,
                             candidate_win_rate=candidate_score.win_rate, summary=reasoning)
    outcome = {
        "promoted": False,
        "reason": "not_better",
        "old_win_rate": current_score.win_rate,
        "candidate_win_rate": candidate_score.win_rate,
        "refine_mode": refine_mode,
    }
    audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
    return outcome


def _archive_current(source: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (config.POLICY_HISTORY_DIR / f"{stamp}.py").write_text(source, encoding="utf-8")

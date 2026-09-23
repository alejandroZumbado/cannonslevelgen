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
from sim import real_suite
from sim.benchmark import fixed_suite, random_suite
from sim.engine import run_level
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

# Real-level failures shown to the LLM per cycle (see sim/real_suite.py).
# They REPLACE the learned-rules block (~470 tokens, mostly restating the
# authoritative rules by 09-22), so the prompt doesn't grow — mind the 413
# history in run_cycle's max_tokens comment before raising this.
_FAILURES_IN_PROMPT = 2

# Refine mode rotates one of these per cycle (2026-09-23): with only "make one
# small change", the LLM proposed the SAME idea (reorder lethal-kill vs deficit
# in the key) in 15/15 cycles of 09-23 and ~all of 09-11..22, even with the
# rejected-attempts list and real failures in the prompt. Each direction
# targets a weakness visible in the real losses (pirates stacked in one
# column, HP above what one cannon can clear in 3 shots).
_EXPLORATION_DIRECTIONS = [
    "merge planning — build up damage in a column BEFORE a high-HP pirate arrives there, "
    "instead of spreading 1-damage cannons.",
    "move actions — when relocating an existing cannon beats placing the new one "
    "(e.g. a column that is empty now vs one about to be overrun).",
    "stacked columns — several pirates queued in one column (blocking): total HP of the "
    "queue vs shots available before the front one reaches the end.",
    "tie-breaking among SAFE actions — which safe action leaves the board most robust "
    "to pirates that may spawn next round in any column.",
    "the deficit estimate itself — make it more accurate (e.g. account for merges/moves "
    "still possible, or for pirates behind the front one getting fewer shots).",
]


def _failures_block(policy, targets: list, seed: int) -> str:
    """Up to _FAILURES_IN_PROMPT real, winnable levels the current policy
    loses, with how it lost. Rotates by `seed` so cycles see different ones."""
    lost = []
    for level in targets:
        engine = run_level(level, policy)
        if not engine.won:
            lost.append((level, engine))
    if not lost:
        return ""
    start = seed % len(lost)
    picked = [lost[(start + i) % len(lost)] for i in range(min(_FAILURES_IN_PROMPT, len(lost)))]
    parts = []
    for level, engine in picked:
        survivors = ", ".join(f"c{p.column} pos{p.position} hp{p.hp}" for p in engine.pirates) or "-"
        parts.append(f"Level {level.levelNumber} (lost at round {engine.round}; pirates left: {survivors}):\n"
                     f"{real_suite.describe_level(level)}")
    return (f"\nReal game levels the current policy LOSES although they are winnable "
            f"({len(lost)} of {len(targets)} such levels; rounds top to bottom, c0=right..c4=left):\n"
            + "\n\n".join(parts) + "\n")

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
                   refine_mode: bool, recent_attempts: str, failures: str = "") -> tuple[str, str]:
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
            "adjust one threshold, add one new condition/branch, or "
            "fix one specific case you can point to. Keep everything else identical. "
            "State in 1-2 sentences exactly which weakness of the current policy (ideally "
            "referencing a concrete column/HP/round scenario) your change addresses.\n"
            f"FOCUS THIS TIME: {_EXPLORATION_DIRECTIONS[rng_seed % len(_EXPLORATION_DIRECTIONS)]}\n"
            "FORBIDDEN (tried 100+ times, never won): changing the priority/order between "
            "lethal-kill flags and the deficit terms in the comparison key."
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

{failures}{recent_block}
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

    # Synthetic suite alone is saturated (see sim/real_suite.py): real
    # winnable levels are where improvement can actually show. Guards (real
    # levels the champion wins) make regressions cost a candidate too.
    targets, guards = real_suite.load_suite()
    suite = (fixed_suite() + random_suite(n=_RANDOM_SUITE_SIZE, seed=datetime.now().microsecond)
             + targets + guards)
    current_score = evaluate(current_policy, suite)
    seed = datetime.now().microsecond
    failures = _failures_block(current_policy, targets, seed)

    streak = strategy_history.consecutive_rejections()
    refine_mode = streak >= _REFINE_MODE_STREAK_THRESHOLD
    # n=4 -> 10 on 2026-09-01, then 10 -> 6 on 2026-09-10 (see max_tokens
    # comment below for why): n=6 still comfortably beats the old n=4's
    # "too narrow" problem from 09-01 (it was missing repeated ideas that
    # only resurfaced every 5-9 cycles) while giving back ~270 tokens of
    # prompt the 413 regression needed.
    # 6 -> 4 on 2026-09-23: the explicit FORBIDDEN line now carries the
    # "don't repeat" signal; the saved ~180 tokens fund max_tokens below.
    recent_attempts = strategy_history.recent_attempts_block(4)

    system, user = _build_prompt(current_source, current_score, rng_seed=seed,
                                  refine_mode=refine_mode, recent_attempts=recent_attempts,
                                  failures=failures)
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
    # the prompt keeps growing over time.
    #
    # Regression 2026-09-06 to 2026-09-10: exactly what the note above warned
    # about happened, but from an angle not tracked here — not knowledge/
    # recent-attempts growth, but current_source itself (the champion policy
    # strategy_learner keeps rewriting) growing from ~6.9KB (09-04) to ~8.6KB
    # via ordinary promotions, adding ~400 tokens on its own. Combined with
    # the already-thin margin, this caused 61 straight 413 Payload Too Large
    # crashes (confirmed in state/error_events.jsonl, 2026-09-06T01:05 through
    # 2026-09-10T00:35) — every single cycle in that window, invisible in
    # `gh run list` since the workflow itself still exits 0 around the
    # exception. Per the note above, fixed by trimming the prompt rather than
    # max_tokens (still 3400, still >= the documented empty-response floor):
    # recent_attempts window 10 -> 6 (see above) and knowledge._MAX_RULES_IN_PROMPT
    # 8 -> 5 (see knowledge.py) together bring worst-case total to ~8078,
    # ~372 tokens under the ceiling — more headroom than the original 230,
    # since current_source has no upper bound and will keep growing as the
    # policy keeps improving. If 413s reappear, re-measure current_source's
    # size first before touching these caps again.
    # 3400 -> 3600 on 2026-09-23: 3/18 cycles that day were no_code_block
    # (code cut off). Prompt shrank ~450 est. tokens since 09-22 (rules block
    # -> failures, attempts 6 -> 4), so worst total stays under the ~8450
    # 413 ceiling measured in the history above.
    completion = client.complete(system, user, max_tokens=3600,
                                  reserve_tokens=config.DAILY_PRODUCTION_RESERVE_TOKENS)
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
    # progress on the real headroom, logged so reviews can see partial gains
    target_wins = ((evaluate(current_policy, targets).wins, evaluate(candidate_policy, targets).wins)
                   if targets else (0, 0))

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
            "target_wins": f"{target_wins[0]}->{target_wins[1]}/{len(targets)}",
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
        "target_wins": f"{target_wins[0]}->{target_wins[1]}/{len(targets)}",
        "refine_mode": refine_mode,
    }
    audit.record_call(caller="strategy_learner", completion=completion, system=system, user=user, outcome=outcome)
    return outcome


def _archive_current(source: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (config.POLICY_HISTORY_DIR / f"{stamp}.py").write_text(source, encoding="utf-8")

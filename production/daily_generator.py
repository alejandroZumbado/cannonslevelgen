"""Month-2+ entry point: one level per day, cheap. Uses the policy and rules
learned during month 1 instead of spending a full day's tokens per level.
Not wired into a scheduler yet on purpose — run manually (or schedule)
once you've decided the learning phase has produced a policy you trust.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
import incident_log
from llm import client, audit
from llm.budget import BudgetExceeded
from llm.client import ProviderQuotaExhausted
from learning import knowledge
from learning.game_rules import GAME_RULES
from policy.loader import load_policy_from_file, PolicyLoadError
from production.cannons_sync import push_generated_level
from production.level_registry import scan_existing_levels, ensure_unique_password
from sim.benchmark import fixed_suite, random_suite
from sim.engine import run_level
from sim.evaluate import evaluate
from sim.level import Level

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"
MAX_ATTEMPTS = 5

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


def _build_prompt(level_number: int) -> tuple[str, str]:
    system = (
        "You design one winnable, well-paced level for the tower-defense game "
        "Cannons. Ground everything in the real rules below — never invent a "
        "mechanic that isn't stated there, no matter how plausible it sounds."
    )
    user = f"""{GAME_RULES}

{_LEVEL_SCHEMA}

Rules confirmed by a month of self-play (trust these over generic guesses,
but never over the authoritative rules above if the two ever disagree):
{knowledge.rules_as_prompt_block()}

Design level number {level_number}. Vary the pattern from typical alternating
left-right layouts; include at least one empty breathing-room fila. Respond
with a one-sentence design note, then the JSON in a single ```json code block.
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


def generate_one_level(level_number: int, used_passwords: set[str]) -> Level | None:
    """Returns None if every attempt was tried and none beat the learned
    policy — a real, expected outcome (see main()), NOT the same thing as
    PolicyLoadError below, which callers should treat as an actual failure
    (there's no point retrying level generation if the policy itself is
    broken) and is deliberately left to propagate rather than being caught
    here.

    `level_number` and `used_passwords` come from level_registry.py scanning
    the REAL Assets/Levels/ contents — don't trust the LLM's own levelNumber
    field (it has no reliable way to know what's already there, and got this
    badly wrong for real on the first cloud run: see level_registry.py's
    docstring)."""
    policy = load_policy_from_file(CURRENT_POLICY_PATH)  # raises PolicyLoadError - let it propagate

    for attempt in range(1, MAX_ATTEMPTS + 1):
        system, user = _build_prompt(level_number)
        completion = client.complete(system, user, max_tokens=3000)
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

        # enforce, don't just ask nicely: the level number is ours to assign
        # (the LLM's guess is discarded), and the password only gets replaced
        # if it actually collides with something already in the game.
        level.levelNumber = level_number
        level.password = ensure_unique_password(level.password, used_passwords)

        engine = run_level(level, policy)
        outcome = {
            "accepted": engine.won, "attempt": attempt, "rounds_played": engine.rounds_played,
            "level_password": level.password,
        }
        audit.record_call(caller="daily_generator", completion=completion, system=system, user=user, outcome=outcome)
        if engine.won:
            print(f"  attempt {attempt}: winnable in {engine.rounds_played} rounds, accepted")
            return level
        print(f"  attempt {attempt}: not winnable by learned policy, retrying")

    return None


def main() -> int:
    exit_code = 0

    if len(sys.argv) > 1:
        level_number = int(sys.argv[1])
        used_passwords: set[str] = set()
    elif config.CANNONS_REPO.exists():
        level_number, used_passwords = scan_existing_levels(config.CANNONS_REPO)
        level_number += 1
        print(f"scanned {config.CANNONS_REPO}: next level number = {level_number}, "
              f"{len(used_passwords)} passwords already in use")
    else:
        # no Cannons checkout available (e.g. running this file standalone
        # without the daily_production.yml workflow's second checkout) —
        # can't know the real next number, so don't guess with something
        # like today's date ordinal again (see level_registry.py docstring
        # for how badly that went the first time).
        print(f"Cannons repo not found at {config.CANNONS_REPO} and no level number given on the "
              f"command line — refusing to guess a level number. Pass one explicitly to test standalone.")
        return 1

    try:
        policy_check = load_policy_from_file(CURRENT_POLICY_PATH)
        suite = fixed_suite() + random_suite(n=100, seed=0)
        champion_score = evaluate(policy_check, suite)
        if champion_score.win_rate < CHAMPION_WIN_RATE_THRESHOLD:
            print(f"champion policy win rate {champion_score.win_rate:.2%} is below the "
                  f"{CHAMPION_WIN_RATE_THRESHOLD:.0%} production threshold — refusing to generate "
                  f"a level today. This is a healthy no-op (learning phase still improving the "
                  f"policy), not an incident.")
            level = None
        else:
            level = generate_one_level(level_number, used_passwords)
    except PolicyLoadError as e:
        # unlike "no candidate won" below, this means production is broken,
        # not just unlucky today — worth a real red X.
        print(f"cannot load current policy: {e}")
        incident_log.record_exception("daily_generator")
        level = None
        exit_code = 1
    except BudgetExceeded as e:
        print(f"budget exhausted for today ({e}) — stopping cleanly, will resume automatically.")
        level = None
    except ProviderQuotaExhausted as e:
        print(f"provider rate-limited hard ({e}) — stopping cleanly, this is Groq/Anthropic's "
              f"cap, not our bug. See state/rate_limit_events.jsonl.")
        level = None
    except Exception:  # noqa: BLE001 — an unexpected bug is still ours, log it and keep going to git_sync
        import traceback
        print("unexpected error generating a level, see traceback below")
        traceback.print_exc()
        incident_log.record_exception("daily_generator")
        level = None
        exit_code = 1

    if level is None:
        if exit_code == 0:
            # every candidate the LLM proposed lost against the learned policy
            # after MAX_ATTEMPTS retries — a real (and logged, and audited)
            # outcome, not a crash. Same principle as NewsPulse's orchestrator:
            # a cycle that produces nothing because nothing cleared the bar is
            # a healthy no-op, not a failure — exit_code stays 0, otherwise
            # this would paint the GitHub Actions run red every day the
            # learned policy is still weak, which is expected early on, not
            # an incident. (exit_code == 1 here instead means PolicyLoadError
            # above — that message already printed, this stays quiet.)
            print("no candidate level beat the learned policy today — nothing to publish, will try again next run")
    else:
        out_path = config.INCOMING_LEVELS_DIR / f"Level_{level.levelNumber}_{datetime.now():%Y%m%d}.json"
        level.save(out_path)
        print(f"wrote {out_path}")

        if not config.CANNONS_REPO.exists():
            print(f"Cannons repo not found at {config.CANNONS_REPO} — file written locally only, "
                  f"not pushed. Set CANNONS_REPO_PATH if this should point somewhere else.")
        else:
            pushed = push_generated_level(config.CANNONS_REPO, out_path)
            if not pushed:
                print("did not push (see reason above) — the level file is still on disk, safe to retry")

    # every attempt (successful or not) spent real tokens and wrote audit/budget
    # state in THIS repo (CannonsLevelGen) — push that back regardless of outcome,
    # same reasoning as run_learning_cycle.py's unconditional git_sync call.
    try:
        import git_sync
        from datetime import timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        git_sync.commit_and_push(f"[bot] daily production run - {now}")
    except Exception:  # noqa: BLE001
        import traceback
        print("git_sync: failed, see traceback below (this run's own audit/budget state may be lost)")
        traceback.print_exc()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

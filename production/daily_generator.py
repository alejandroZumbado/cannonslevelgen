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
from llm import client, audit
from learning import knowledge
from policy.loader import load_policy_from_file, PolicyLoadError
from production.cannons_sync import push_generated_level
from sim.engine import run_level
from sim.level import Level

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"
MAX_ATTEMPTS = 5

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
    system = "You design one winnable, well-paced level for the tower-defense game Cannons."
    user = f"""{_LEVEL_SCHEMA}

Rules confirmed by a month of self-play (trust these over generic guesses):
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


def generate_one_level(level_number: int) -> Level | None:
    try:
        policy = load_policy_from_file(CURRENT_POLICY_PATH)
    except PolicyLoadError as e:
        print(f"cannot load current policy: {e}")
        return None

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
    level_number = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now().toordinal()
    exit_code = 0
    level = generate_one_level(level_number)

    if level is None:
        print("failed to produce a winnable level today")
        exit_code = 1
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

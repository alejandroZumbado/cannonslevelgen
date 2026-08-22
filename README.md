# CannonsLevelGen

AI pipeline that learns to play [Cannons](../Cannons) (a Unity tower-defense
game) through self-play, then uses what it learned to generate one new,
validated-winnable level per day. See `../Cannons/CLAUDE.md` for how the game
itself works — this project mirrors those rules in pure Python so play can be
simulated without Unity.

## Two phases

**Month 1 — learning (all tokens go here, no levels produced yet):**
Two loops run continuously, one LLM call each, alternating:
- `learning/strategy_learner.py` — the AI rewrites `policy/current.py` (the
  cannon placement/merge strategy), benchmarks the rewrite for free against
  `sim/benchmark.py` (thousands of simulated rounds, zero tokens), and keeps
  it only if it wins more often than what's there now.
- `learning/level_designer.py` — the AI proposes a level-design hypothesis
  and a level built to test it, plays it with the current best policy, and
  records the empirical result to `knowledge/level_rules_learned.json`.

Both are driven by `run_learning_cycle.py`, meant to run every ~15 minutes,
all day, for about a month — see `scripts/register_task_scheduler.ps1`.
The daily token budget (`llm/budget.py`, default 180k tokens/day, under
Groq's 200k free-tier cap) makes this safe to leave running unattended: once
a day's budget is spent, cycles no-op until the next calendar day.

Progress is human-readable in `learning_log/YYYY-MM-DD.md` (one file per day)
— read that for a quick check-in. For a full audit — every call made, exact
token cost, complete prompt/response, and what it achieved — see `audit/`
(`python audit/report.py` for a readable rollup, `audit/README.md` for how it
relates to `learning_log/` and `state/budget.json`).

**Month 2+ — daily production (cheap, ~1 LLM call/day):**
`production/daily_generator.py` uses the policy and rules learned in month 1
to generate and validate one level, then drops it as JSON in
`../Cannons/GeneratedLevels/incoming/`.

## Getting it into the actual game

Both projects run on the same machine, so delivery is a plain local file, no
git involved. In the Unity Editor: `Levels > Import Generated Levels (JSON)`
(`Cannons/Assets/Editor/LevelImporter.cs`) reads everything in
`GeneratedLevels/incoming/`, creates a `Level` ScriptableObject + registers it
in `LevelDatabase` the same way the existing `LevelGenerator.cs` does, and
moves the source JSON to `GeneratedLevels/processed/`. Nothing is auto-added
to the game without you running that menu item.

## Setup

```
pip install -r requirements.txt
python tests/test_engine.py        # sanity-check the simulator matches the game's rules
```

`.env` already has `GROQ_API_KEY` / `ANTHROPIC_API_KEY` (moved here from
`Cannons/.env`, which no longer has them). `AI_PROVIDER` picks which one
`llm/client.py` calls; Groq is free-tier, Anthropic is pay-per-token (used as
overflow if you ever want to burn past the Groq daily cap on a given day).

To start the month-1 learning phase for real:
```
.\scripts\register_task_scheduler.ps1
```
This registers a Windows Task Scheduler job. It is NOT run automatically by
anything else — you run it once, deliberately, when ready.

To run a single cycle manually (e.g. to sanity-check the wiring before
committing to a month):
```
python run_learning_cycle.py
```

## Layout

```
sim/            headless game engine — level schema, round simulation, benchmark suites, scoring
policy/         current.py = active strategy (AI-rewritten), baseline.py = fixed v0 reference, history/ = every past version
llm/            Groq/Anthropic REST client + persisted daily token budget
learning/       the two month-1 loops + the knowledge base they write to
production/     month-2+ daily level generator
knowledge/      level_rules_learned.json — accumulated, AI-discovered design rules
learning_log/   one markdown file per day — human-readable progress log
audit/          one JSONL file per day — full per-call audit trail (exact tokens, full prompt/response, outcome) + report.py to read it
state/          budget.json (daily token counter) — not meant to be read directly
```

## A note on trust

`policy/loader.py` execs AI-generated Python with a restricted builtins list.
That's a safety net against accidentally broken code (stray `open()`,
`import os`), not a real security sandbox — treat generated policy code with
the same trust you'd give code you pasted into your own REPL. This runs
locally under your own API keys; don't point it at untrusted policy files
from anywhere else.

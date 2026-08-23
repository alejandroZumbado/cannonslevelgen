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

Both are driven by `run_learning_cycle.py`, run every ~15 minutes, all day,
by **`.github/workflows/learning.yml` — GitHub Actions, not your own PC.**
Each run gets a fresh disposable VM; there is no local state to lose, because
every run ends by committing+pushing whatever it learned back to this repo
(`git_sync.py`, same pattern as the NewsPulse project's `publisher.py`) —
the next run, on a different disposable VM, starts by checking that out.
This means the learning phase keeps going even with your computer off.
(`scripts/register_task_scheduler.ps1` still exists for local-machine testing
— see "Running locally vs. in the cloud" below — but it is NOT what actually
runs the month-long phase.)

The daily token budget (`llm/budget.py`, default 180k tokens/day, under
Groq's 200k free-tier cap) is itself part of what gets committed
(`state/budget.json`) — without that, every disposable VM would think it had
a fresh 180k-token day, and the free-tier cap would mean nothing.

Progress is human-readable in `learning_log/YYYY-MM-DD.md` (one file per day)
— read that for a quick check-in, from anywhere, since it's just a file in
this repo now. For a full audit — every call made, exact token cost,
complete prompt/response, and what it achieved — see `audit/`
(`python audit/report.py` for a readable rollup, `audit/README.md` for how it
relates to `learning_log/` and `state/budget.json`).

**Month 2+ — daily production (cheap, ~1 LLM call/day):**
`production/daily_generator.py` uses the policy and rules learned in month 1
to generate and validate one level, then drops it as JSON in
`../Cannons/GeneratedLevels/incoming/`.

## Getting it into the actual game

NOT wired up yet (deliberately deferred — month 1 doesn't produce levels).
`production/daily_generator.py` currently writes straight to a local
`../Cannons/GeneratedLevels/incoming/` folder, which only makes sense if this
project and Cannons are checked out on the same machine. Since the learning
phase moved to GitHub Actions (see above), month 2's delivery needs the same
treatment: `daily_generator.py` pushing its one JSON/day to the `mandrix/cannons`
GitHub repo (which already exists — `git@github.com:mandrix/cannons.git`),
so you `git pull` it locally and run `Levels > Import Generated Levels
(JSON)` (`Cannons/Assets/Editor/LevelImporter.cs`) in the Unity Editor. Not
built yet — revisit this before month 2.

## Setup

```
pip install -r requirements.txt
python tests/test_engine.py        # sanity-check the simulator matches the game's rules
```

`.env` already has `GROQ_API_KEY` / `ANTHROPIC_API_KEY` (moved here from
`Cannons/.env`, which no longer has them) — used for local runs. In GitHub
Actions the same values live as **repo secrets**, not in `.env` (which is
gitignored and never leaves your machine). `AI_PROVIDER` picks which one
`llm/client.py` calls; Groq is free-tier, Anthropic is pay-per-token (used as
overflow if you ever want to burn past the Groq daily cap on a given day).

The month-1 learning phase runs on its own via
`.github/workflows/learning.yml` once this repo is pushed to GitHub with
those secrets set — nothing further to run or leave open on your machine.

### Running locally vs. in the cloud

Running `python run_learning_cycle.py` locally still works exactly like the
GitHub Actions run does — same code, same git_sync.py push at the end — it's
just one more contributor to the same repo's history, useful for testing a
change to the learning loop itself before it goes out to the scheduled job.
`scripts/register_task_scheduler.ps1` (Windows Task Scheduler, every 15 min)
is kept only for that kind of local testing loop; **do not run it at the same
time as the GitHub Actions workflow is enabled** — both would spend against
the same Groq key with no coordination between them, since each only knows
about the budget state in its own worktree until the next push/pull.

## Layout

```
.github/workflows/learning.yml   cron (every 15 min) + manual trigger, runs on GitHub's own VMs
git_sync.py     commits+pushes runtime state back to this repo at the end of every cycle
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

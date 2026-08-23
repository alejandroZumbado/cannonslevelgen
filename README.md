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
to generate and validate one level, then pushes it as JSON into
`GeneratedLevels/incoming/` in the Cannons repo itself — see "Getting it into
the actual game" below for how.

## Getting it into the actual game

Wired up and verified with real cloud runs: `.github/workflows/daily_production.yml`
checks out both this repo and `alejandroZumbado/cannons` on the same disposable
runner, generates one validated level using `production/level_registry.py` to
pick the real next `levelNumber` and avoid password collisions (scanned from
the actual `Assets/Levels/*.asset` files — don't reintroduce a guessed number
like `datetime.now().toordinal()`, that shipped a `levelNumber: 739851` into
the real game repo once already), and `production/cannons_sync.py` pushes it
straight to `GeneratedLevels/incoming/` there. You `git pull` it locally and
run `Levels > Import Generated Levels (JSON)`
(`Cannons/Assets/Editor/LevelImporter.cs`) in the Unity Editor — untested
inside the actual Unity Editor as of this writing, verify it there before
trusting it blindly. The `schedule` trigger in `daily_production.yml` is
still commented out on purpose (see the file) until month 1's learned policy
is trusted enough to publish into the game repo unattended.

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

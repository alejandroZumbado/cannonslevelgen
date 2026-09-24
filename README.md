# CannonsLevelGen

AI pipeline that learns to play [Cannons](../Cannons) (a Unity tower-defense
game) through self-play, then uses what it learned to generate one new,
validated-winnable level per day. See `../Cannons/CLAUDE.md` for how the game
itself works — this project mirrors those rules in pure Python so play can be
simulated without Unity.

## Two phases

> **PAUSED since 2026-09-24:** `learning.yml` has no schedule (manual button
> only). strategy_learner went 13 days without a promotion (target_wins
> 0->0/32 on the real hard suite) while using ~75% of the daily budget. The
> champion `policy/current.py` (09-11) keeps serving production/verification;
> daily_production and weekly_level_audit are unaffected. To resume, restore
> the `schedule:` block in `learning.yml`.

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
straight to `GeneratedLevels/incoming/` there (scheduled daily since
2026-09-13). You `git pull` it locally and run `Levels > Import Generated
Levels (JSON)` (`Cannons/Assets/Editor/LevelImporter.cs`, verified in real
Unity runs, also headless) — imported levels land in the **reserve**, not in
the playable release, until linked (next section).

## Fun gate: pacing + variety (`verification/pacing.py`, since 2026-09-24)

Winnable is not enough (Level 508: 27 rounds, one pirate each — winnable,
trivial, boring). Both generators now reject levels that break:
- **pressure keeps up with firepower**: the player gains a cannon every round,
  so `late_demand` (danger of the last third ÷ cannons owned, see module
  docstring) must be >= 0.7 — otherwise the level gets easier as it goes;
- **width over height**: <= 50% single-pirate rounds (2 x HP7 > 1 x HP14);
- **<= 12 rounds**.

Variety: 6 archetypes (swarm, tank, wall, crescendo, burst, switch) detected
from structure. `daily_generator` asks the LLM for the one least used in the
newest 20 levels and, when an attempt is rejected, feeds the exact reasons
into the next attempt (8 attempts). `batch_generator` ramps pirates per round
toward the end and caps any one archetype at 35% of a batch.
`verification/variety_report.py` (weekly, before the audit) writes
`reports/variety/latest.json`: pacing failures in the release by position,
archetype mix, repetitive arcs. First run: 104/200 release levels pass.

## Whole-campaign regulation (since 2026-09-24)

User decision: drop the curated 200, rework ALL levels (509) and ship every
winnable one. `.github/workflows/level_regulation.yml` (manual trigger):
1. **regulate** — 8 parallel shards of `verification/regulator.py`. Per
   level: kept if winnable + pacing ok; the easiest short simple ones kept as
   breathers (max 15% of the campaign); everything else goes through a local
   search of small edits (`verification/level_mutations.py`), each SIMULATED
   (champion, then solver) — broken levels become winnable (target: hard but
   champion-winnable), boring ones get tension back, and each reworked level
   aims at the archetype the campaign lacks most. Resumable, results in
   `reports/regulation/pass1/shard_*.json`.
2. **apply** — `verification/apply_regulation.py` rewrites only the `filas:`
   block of each asset (verified by parsing back), pushes to `cannons`.
3. **finalize** — `verification/regulation_finalize.py`: re-audits every level,
   regulator pass 2 repairs anything still broken/empty, re-curates the WHOLE
   campaign (`curate_release --rebuild-all`), rewrites `LevelDatabase` +
   isHard, variety report, pushes.

Learning loops now serve the regulation: `learning/regulation_designer.py`
(replaces level_designer in `run_learning_cycle.py`, 1 of 2 cycles) asks the
LLM to redesign levels the regulator left unresolved/partial/off-archetype,
verified with the same fitness; accepted ones go to
`reports/regulation/llm_proposals.json` and are applied on the next
`apply_regulation` run. Its verified wins/rejections are kept in
`knowledge/regulation_lessons.json` and shown to the next cycle.
strategy_learner trains on the re-audited levels (`reports/real_suite.json`).

 (`verification/`, `production/`)

The game's `LevelDatabase` holds only the shipped release (200 levels since
2026-09-22), ordered in difficulty arcs — see Cannons' `CLAUDE.md`
("Release curation", "Adding a batch of levels") for the game side.

- `python -m production.batch_generator --count 100` — LLM-free batch of
  verified levels (champion + solver), written to Cannons' `incoming/`.
- `python -m verification.extend_release --count 100 [--dry-run]` — after
  the Unity import: refreshes the audit if needed, appends the levels after
  the last position (never moves existing ones), rewrites
  `LevelDatabase.asset`, runs Unity's `ReleaseValidator` headless.
- `python -m verification.curate_release` — first release only (refuses once
  anything is assigned). Output: `reports/campaign_manifest.json` (pools:
  assigned/ready/roto/problema_vacio) and `reports/release_order.json`.
- `sim/real_suite.py` / `reports/real_suite.json` — the real game's
  winnable levels, written by the weekly audit; strategy_learner scores
  candidates on them because the synthetic suite is saturated.

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
.github/workflows/learning.yml            cron + manual trigger, loops cycles until the daily token budget is spent
.github/workflows/daily_production.yml    cron (daily 06:00 UTC) — 1 LLM-designed level into Cannons' incoming/
.github/workflows/weekly_level_audit.yml  cron (Mon 08:00 UTC) — plays every real Cannons level, no LLM calls
.github/workflows/level_repair.yml / level_rebalance.yml  manual only — level fix tools (already applied 09-15)
git_sync.py     commits+pushes runtime state back to this repo at the end of every cycle
sim/            headless game engine — level schema, round simulation, benchmark suites, scoring
policy/         current.py = active strategy (AI-rewritten), baseline.py = fixed v0 reference, history/ = every past version
llm/            Groq/Anthropic REST client + persisted daily token budget
learning/       the two month-1 loops + the knowledge base they write to
production/     month-2+ daily level generator
verification/   weekly audit of the real 500-level campaign — see below
knowledge/      level_rules_learned.json — accumulated, AI-discovered design rules
learning_log/   one markdown file per day — human-readable progress log
audit/          one JSONL file per day — full per-call audit trail (exact tokens, full prompt/response, outcome) + report.py to read it
reports/        level_audit/ (weekly runs + latest.json), campaign_manifest.json, release_order.json, real_suite.json
state/          budget.json (daily token counter) — not meant to be read directly
```

## Weekly level audit (`verification/`)

Separate from everything above — no LLM calls, pure simulation, so it costs
nothing against the daily token budget. Once a week
(`.github/workflows/weekly_level_audit.yml`), plays all 500 REAL shipped
Cannons levels (not the synthetic benchmark suites used to score policy
candidates during learning) with two independent signals:

- the current champion policy (`policy/current.py`) exactly as trained, and
- an independent wide beam search over the real rules (`verification/solver.py`),
  run only on levels the champion loses, to tell apart "the AI isn't good
  enough here yet" from "this level might actually be broken".

Writes `reports/level_audit/<date>.json` + `latest.json`, comparing against
the previous run so you can see whether real-campaign coverage is
improving, plateaued, or regressing over time — independent of whatever the
benchmark win_rate in `knowledge/strategy_history.json` says. Run manually
with `python -m verification.level_audit` (needs `CANNONS_REPO_PATH` set to
a checkout of `alejandroZumbado/cannons`, same convention as the rest of
this project).

## A note on trust

`policy/loader.py` execs AI-generated Python with a restricted builtins list.
That's a safety net against accidentally broken code (stray `open()`,
`import os`), not a real security sandbox — treat generated policy code with
the same trust you'd give code you pasted into your own REPL. This runs
locally under your own API keys; don't point it at untrusted policy files
from anywhere else.

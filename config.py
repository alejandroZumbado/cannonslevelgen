"""Loads .env (no external dependency — a hand-rolled parser is enough for
KEY=VALUE lines) and exposes project-wide constants."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_env(ENV_PATH)

AI_PROVIDER = os.environ.get("AI_PROVIDER", "groq")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# Groq free tier for openai/gpt-oss-120b (verified 2026-08-21, see .claude/groq-status.md
# in the Cannons repo — re-check console.groq.com/docs/models if this pipeline starts
# getting 429s, the catalog has changed before without notice).
GROQ_RPM_LIMIT = 30
GROQ_TPM_LIMIT = 8_000
GROQ_RPD_LIMIT = 1_000
GROQ_TPD_LIMIT = 200_000

# Stay under the daily cap with margin so a slightly-off token estimate never
# causes a hard 429 mid-cycle.
DAILY_TOKEN_BUDGET = int(os.environ.get("DAILY_TOKEN_BUDGET", 180_000))

# Slice of DAILY_TOKEN_BUDGET that learning.yml's hourly cycles must never
# touch, reserved for daily_production.yml. Found 2026-09-14/15: the two
# workflows share this same daily counter, and once GitHub actually grants a
# learning run it loops pairs until the budget runs out BY DESIGN (see
# run_learning_cycle.py) — measured burning ~176k/180k tokens in a single
# 20min run right after the UTC day rolled over, hours before
# daily_production's own 6am UTC cron. Without a reservation, production is
# starved most days regardless of what time it's scheduled at. Sized at
# ~5x a single daily_generator attempt (measured 2026-09-15: ~4,730 tokens
# per attempt incl. GAME_RULES + up to 8 learned rules; MAX_ATTEMPTS=5 there)
# so a full worst-case day of retries still fits. Only learning callers pass
# this as `reserve_tokens` to llm.client.complete() — daily_generator itself
# calls with the default (0), so it can freely spend into its own reserved
# slice (and anything extra learning left unspent).
DAILY_PRODUCTION_RESERVE_TOKENS = int(os.environ.get("DAILY_PRODUCTION_RESERVE_TOKENS", 25_000))

# If a 429's own retry-after exceeds this, llm/client.py treats it as a hard
# provider-side quota (daily/hourly cap), not the known TPM burst, and gives
# up immediately instead of sleeping through it. Learned the hard way on
# 2026-08-23: a genuine TPM 429 backs off for single-digit-to-low-double-digit
# seconds; sleeping past this instead means the job's own timeout-minutes
# kills it mid-sleep with zero clean output. See llm/rate_limits.py.
MAX_RETRY_WAIT_SECONDS = 90.0

# How long run_learning_cycle.py is allowed to keep looping cycles within a
# single invocation before it stops on its own and lets the job exit cleanly.
# Added 2026-08-29: this repo/account is new enough that GitHub's scheduler
# batches it into a low-priority queue swept only ~1-2x/day regardless of the
# cron interval requested (same symptom as github.com/orgs/community/
# discussions/201738). Asking for a shorter interval doesn't buy more real
# firings, so each firing now loops many cycles back-to-back instead of just
# one, to actually spend the day's token budget when GitHub does grant a run.
# Set well below the workflow's timeout-minutes (see learning.yml) so there's
# always time left for a final git push before GitHub kills the job. The
# default here (no env var set) is for local/manual runs, which don't need a
# long loop.
JOB_TIME_BUDGET_SECONDS = int(os.environ.get("JOB_TIME_BUDGET_SECONDS", 480))

CANNONS_REPO = Path(os.environ.get("CANNONS_REPO_PATH", ROOT.parent / "Cannons"))
INCOMING_LEVELS_DIR = CANNONS_REPO / "GeneratedLevels" / "incoming"

# Local Unity Editor, used only by verification/extend_release.py to run the
# ReleaseValidator headless. Override with UNITY_EXE; missing = step skipped.
UNITY_EXE = os.environ.get("UNITY_EXE", r"E:\versionesUnity\6000.3.13f1\Editor\Unity.exe")

STATE_DIR = ROOT / "state"
KNOWLEDGE_DIR = ROOT / "knowledge"
LEARNING_LOG_DIR = ROOT / "learning_log"
POLICY_HISTORY_DIR = ROOT / "policy" / "history"
AUDIT_DIR = ROOT / "audit"

for d in (STATE_DIR, KNOWLEDGE_DIR, LEARNING_LOG_DIR, POLICY_HISTORY_DIR, INCOMING_LEVELS_DIR, AUDIT_DIR):
    d.mkdir(parents=True, exist_ok=True)

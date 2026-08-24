"""Minimal REST client for Groq (OpenAI-compatible) and Anthropic — no SDK
dependency, just `requests`. Every call goes through the persisted daily
budget (llm/budget.py) so a month of unattended Task Scheduler runs can never
overspend the free tier.

Gotcha (verified empirically): `openai/gpt-oss-120b` does internal reasoning
that counts against `max_tokens` before any visible content is emitted — a
too-small max_tokens silently returns an empty string, not an error. Callers
in this project use max_tokens >= 3000 for exactly this reason; don't lower
those without re-testing.

Second gotcha (also verified empirically, not just from docs): Groq's free
tier limits tokens-PER-MINUTE (8000 TPM) independently of requests-per-minute
— two back-to-back ~4000-token calls (well within the 30 RPM cap) already
trip a 429 on TPM alone. _call_groq retries on 429 using the server's own
"try again in Ns" hint instead of surfacing the error, since this pipeline is
meant to run unattended for a month and a transient TPM trip should never
kill a whole learning cycle.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

import config
from llm import budget, cooldown, rate_limits

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


@dataclass
class Completion:
    text: str
    tokens_used: int          # exact figure for THIS call, for per-call audit logging
    provider: str
    model: str

_MIN_SECONDS_BETWEEN_CALLS = 60.0 / config.GROQ_RPM_LIMIT  # safety throttle, same process only
_last_call_time = 0.0


class LLMError(Exception):
    pass


class ProviderQuotaExhausted(LLMError):
    """A 429 whose own retry-after is longer than config.MAX_RETRY_WAIT_SECONDS
    — treated as a hard provider-side cap (daily/hourly), not the known TPM
    burst. Callers should treat this like budget.BudgetExceeded: stop the
    cycle cleanly, don't retry. See llm/rate_limits.py for why this exists."""
    pass


def _throttle() -> None:
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < _MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(_MIN_SECONDS_BETWEEN_CALLS - elapsed)
    _last_call_time = time.monotonic()


def _estimate_tokens(*texts: str) -> int:
    # rough ~4 chars/token estimate, good enough for budget gating (not billing)
    return sum(len(t) for t in texts) // 4


def complete(system: str, user: str, max_tokens: int = 2000, provider: str | None = None) -> Completion:
    """Returns a Completion (text + exact tokens_used for this call). Raises
    budget.BudgetExceeded if the call would blow the daily cap — callers
    should catch that and stop the cycle gracefully, not retry.

    Callers are expected to pass every field of the returned Completion,
    together with the outcome they derived from it, to llm.audit.record_call
    — this function only handles transport + the aggregate daily counter, it
    does not write the per-call audit trail itself (the caller knows the
    outcome, this function doesn't).

    Also raises ProviderQuotaExhausted WITHOUT making any HTTP request if
    `provider` is still inside a persisted cooldown from a previous hard 429
    (see llm/cooldown.py) — once a provider tells us its quota is exhausted
    for N minutes, every call attempt in that window (including from a brand
    new disposable CI runner with no memory of its own) skips straight to
    this instead of spending another guaranteed-429 request."""
    provider = provider or config.AI_PROVIDER

    cooling_until = cooldown.resume_at(provider)
    if cooling_until is not None:
        remaining = (cooling_until - datetime.now(timezone.utc)).total_seconds()
        rate_limits.record(provider=provider, attempt=-1, wait_seconds=remaining, gave_up=True,
                            detail=f"skipped call entirely — persisted cooldown active until "
                                   f"{cooling_until.isoformat(timespec='seconds')}")
        raise ProviderQuotaExhausted(
            f"{provider} is in a persisted cooldown until {cooling_until.isoformat(timespec='seconds')} "
            f"({remaining:.0f}s left) — skipping this call rather than spamming a provider we "
            f"already know is out of quota."
        )

    estimated_in = _estimate_tokens(system, user)
    budget.check_can_spend(estimated_in + max_tokens)

    if provider == "groq":
        text, used = _call_groq(system, user, max_tokens)
        model = config.GROQ_MODEL
    elif provider == "anthropic":
        text, used = _call_anthropic(system, user, max_tokens)
        model = config.ANTHROPIC_MODEL
    else:
        raise LLMError(f"unknown AI_PROVIDER {provider!r}")

    budget.record_usage(used)
    return Completion(text=text, tokens_used=used, provider=provider, model=model)


_RETRY_SECONDS_RE = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)
_MAX_429_RETRIES = 4


def _parse_retry_after(resp) -> float:
    header = resp.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = _RETRY_SECONDS_RE.search(resp.text)
    if match:
        return float(match.group(1))
    return 5.0  # unknown reason for the 429, back off a bit anyway


def _call_groq(system: str, user: str, max_tokens: int) -> tuple[str, int]:
    if not config.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY not set in .env")

    for attempt in range(_MAX_429_RETRIES + 1):
        _throttle()
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={
                "model": config.GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=120,
        )
        if resp.status_code == 429:
            wait = _parse_retry_after(resp) + 1.0  # small margin over the server's own estimate
            if wait > config.MAX_RETRY_WAIT_SECONDS:
                rate_limits.record(provider="groq", attempt=attempt, wait_seconds=wait,
                                    gave_up=True, detail=resp.text)
                cooldown.set_cooldown("groq", datetime.now(timezone.utc) + timedelta(seconds=wait),
                                       detail=resp.text)
                raise ProviderQuotaExhausted(
                    f"Groq 429 with retry-after={wait:.0f}s exceeds the "
                    f"{config.MAX_RETRY_WAIT_SECONDS:.0f}s threshold — looks like a daily/hourly "
                    f"cap, not a TPM burst. Giving up now instead of sleeping past this job's "
                    f"timeout, and remembering not to try again until then. Raw: {resp.text[:300]}"
                )
            rate_limits.record(provider="groq", attempt=attempt, wait_seconds=wait,
                                gave_up=(attempt == _MAX_429_RETRIES), detail=resp.text)
            if attempt == _MAX_429_RETRIES:
                raise LLMError(f"Groq rate-limited (429) after {attempt} retries: {resp.text[:300]}")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        cooldown.clear("groq")
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        used = data.get("usage", {}).get("total_tokens") or _estimate_tokens(system, user, text)
        return text, used

    raise LLMError("unreachable")  # loop always returns or raises above


def _call_anthropic(system: str, user: str, max_tokens: int) -> tuple[str, int]:
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY not set in .env")

    for attempt in range(_MAX_429_RETRIES + 1):
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=120,
        )
        if resp.status_code == 429:
            wait = _parse_retry_after(resp) + 1.0
            if wait > config.MAX_RETRY_WAIT_SECONDS:
                rate_limits.record(provider="anthropic", attempt=attempt, wait_seconds=wait,
                                    gave_up=True, detail=resp.text)
                cooldown.set_cooldown("anthropic", datetime.now(timezone.utc) + timedelta(seconds=wait),
                                       detail=resp.text)
                raise ProviderQuotaExhausted(
                    f"Anthropic 429 with retry-after={wait:.0f}s exceeds the "
                    f"{config.MAX_RETRY_WAIT_SECONDS:.0f}s threshold — treating as a hard cap, "
                    f"remembering not to try again until then. Raw: {resp.text[:300]}"
                )
            rate_limits.record(provider="anthropic", attempt=attempt, wait_seconds=wait,
                                gave_up=(attempt == _MAX_429_RETRIES), detail=resp.text)
            if attempt == _MAX_429_RETRIES:
                raise LLMError(f"Anthropic rate-limited (429) after {attempt} retries: {resp.text[:300]}")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        cooldown.clear("anthropic")
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []))
        usage = data.get("usage", {})
        used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        return text, used or _estimate_tokens(system, user, text)

    raise LLMError("unreachable")  # loop always returns or raises above

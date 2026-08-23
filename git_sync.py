"""Commits and pushes this project's own runtime state (policy/, knowledge/,
learning_log/, audit/, state/budget.json) back to its own GitHub repo.

Same pattern as the NewsPulse project's pipeline/publisher.py: this runs on
ephemeral GitHub Actions runners, so nothing survives between scheduled runs
except what gets committed back before the runner is torn down. Locally
(same-machine testing) this is a no-op convenience, not the source of truth
— on GitHub Actions it's the ONLY thing making a month of learning cumulative
instead of each run starting from scratch.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent


def run_git(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(["git"] + args, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def configure_git_once() -> None:
    run_git(["config", "user.email", "aazv.ale@gmail.com"])
    run_git(["config", "user.name", "CannonsLevelGen Bot"])


def commit_and_push(message: str) -> bool:
    """Stages every tracked change (policy/current.py rewrites, new knowledge,
    new logs, new audit entries, updated budget) and pushes. Returns False
    (not an error) if there was nothing to commit — most cycles that don't
    promote a policy still touch knowledge/audit/state, so this is mainly
    for the rare fully-idle cycle."""
    configure_git_once()

    code, out = run_git(["add", "-A"])
    if code != 0:
        print(f"git add -A failed: {out}")
        return False

    code, status = run_git(["diff", "--cached", "--name-only"])
    if not status.strip():
        print("git_sync: nothing to commit")
        return False

    code, out = run_git(["commit", "-m", message])
    if code != 0:
        print(f"git commit failed: {out}")
        return False

    # pull --rebase AFTER commit (it refuses to run with staged changes,
    # which is exactly the state right after a commit is not — but a
    # concurrent run could have pushed in between commit and push here).
    code, out = run_git(["pull", "--rebase", "origin", "main"])
    if code != 0:
        print(f"git pull --rebase failed, aborting rebase: {out}")
        run_git(["rebase", "--abort"])
        return False

    # actions/checkout in CI already configures push credentials on `origin`
    # (token-based); locally this repo is already on an SSH remote (same as
    # every other manual push made in this project), so plain push works
    # either way — no token juggling needed.
    code, out = run_git(["push", "origin", "main"])
    if code != 0:
        print(f"git push failed: {out}")
        return False

    print(f"git_sync: pushed - {message}")
    return True


def sync_cycle_results() -> bool:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return commit_and_push(f"[bot] learning cycle - {now}")

"""Commits and pushes a generated level file into the Cannons game repo
itself (not this project's own repo — see git_sync.py for that).

Cannons lives at github.com/alejandroZumbado/cannons, a separate repo from this one.
The daily production workflow (.github/workflows/daily_production.yml)
checks it out into a side directory using a token with push access to BOTH
repos (secrets.CANNONS_PUSH_TOKEN — the default per-workflow GITHUB_TOKEN
only has permission scoped to the repo the workflow runs in, which isn't
enough for a cross-repo push). This module just does `git add/commit/push`
inside that checked-out directory, same shape as git_sync.py.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def push_generated_level(cannons_repo_path: Path, level_file: Path) -> bool:
    """Stages just the one new level JSON under GeneratedLevels/incoming/ in
    the Cannons checkout and pushes it to main. Returns False (not an error)
    if nothing was actually new to commit."""
    rel_path = level_file.relative_to(cannons_repo_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return push_paths(cannons_repo_path, [level_file], f"[bot] Add AI-generated level {rel_path.name} - {now}")


def push_paths(cannons_repo_path: Path, paths: list[Path], message: str) -> bool:
    """Stages exactly `paths` (files or folders) in the Cannons checkout,
    commits and pushes to master. Used for single level drops and for the
    regulator's bulk edits of Assets/Levels (2026-09-24). Returns False (not
    an error) if nothing changed; prints the reason on any git failure."""
    run_git(["config", "user.email", "aazv.ale@gmail.com"], cwd=cannons_repo_path)
    run_git(["config", "user.name", "CannonsLevelGen Bot"], cwd=cannons_repo_path)

    for path in paths:
        rel_path = path.relative_to(cannons_repo_path)
        code, out = run_git(["add", "--", str(rel_path)], cwd=cannons_repo_path)
        if code != 0:
            print(f"cannons_sync: git add {rel_path} failed: {out}")
            return False

    code, status = run_git(["diff", "--cached", "--name-only"], cwd=cannons_repo_path)
    if not status.strip():
        print("cannons_sync: nothing to commit")
        return False

    code, out = run_git(["commit", "-m", message], cwd=cannons_repo_path)
    if code != 0:
        print(f"cannons_sync: git commit failed: {out}")
        return False

    code, out = run_git(["pull", "--rebase", "origin", "master"], cwd=cannons_repo_path)
    if code != 0:
        print(f"cannons_sync: git pull --rebase failed, aborting rebase: {out}")
        run_git(["rebase", "--abort"], cwd=cannons_repo_path)
        return False

    code, out = run_git(["push", "origin", "master"], cwd=cannons_repo_path)
    if code != 0:
        print(f"cannons_sync: git push failed: {out}")
        return False

    print(f"cannons_sync: pushed \"{message}\" to alejandroZumbado/cannons")
    return True

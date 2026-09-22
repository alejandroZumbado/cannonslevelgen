"""Generates a BATCH of new levels locally — no LLM, no tokens, no cost —
for the next release extension (e.g. 100 levels at once).

    python -m production.batch_generator --count 100 [--seed 7] [--dry-run]

Why: the daily LLM generator makes ~1 level/day, and the ready reserve
only covers ~20 more release slots. This fills the gap with procedurally
drawn levels that are each VERIFIED by simulation before being kept:

  1. Draw a candidate shaped like the shipped levels (5-10 rounds, 1-4
     pirates per round, mostly low HP, every round has pirates, exactly one
     tipo-4 "last pirate" on the final round).
  2. Reject reskins of any existing level or of another batch level.
  3. Play it with the trained champion policy (sim/engine.py). A win is
     scored with the same audit code as the weekly audit
     (verification/level_audit.audit_level). A loss is only kept if the
     wide search solver wins it AND the batch still needs spike levels
     (solved_by_search_only = arc peaks, one per ~11 levels).
  4. Fill difficulty quotas skewed HARDER than the first release, since
     these levels go after position 200.

Writes JSON to Cannons/GeneratedLevels/incoming/ (same format as the daily
generator), which then goes through the normal path: Unity import ->
`python -m verification.extend_release --count N`. Levels are never put
straight into the release from here.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from policy.loader import load_policy_from_file
from production.level_registry import ensure_unique_password, scan_existing_levels, scan_existing_signatures
from sim.engine import run_level
from sim.level import Cuadro, Fila, Level
from verification.level_audit import audit_level
from verification.solver import DEFAULT_MAX_ROUNDS, ESCALATION_BEAM_WIDTHS

CURRENT_POLICY_PATH = config.ROOT / "policy" / "current.py"
REPORT_DIR = config.ROOT / "reports" / "batch_generation"

# Champion-win difficulty_score bins and the share of the champion part of
# the batch each should get. The first release tops out around 16-17, so the
# weight sits in the upper bins.
SCORE_BINS: list[tuple[float, float, float]] = [
    (0.0, 11.0, 0.10),
    (11.0, 13.0, 0.25),
    (13.0, 15.0, 0.35),
    (15.0, math.inf, 0.30),
]
ARC_SIZE = 11  # one search-only spike per arc, matches curate_release.ARC_SIZE
MAX_ATTEMPTS_PER_LEVEL = 400  # guard so an impossible quota fails instead of looping forever
HARD_SCORE = 16.0  # champion levels at/above this are flagged isHard (hard-level music)


def _draw_candidate(rng: random.Random) -> Level:
    """One random level shaped like the shipped ones. `t` in [0,1] pushes
    rounds, pirates per round and HP up together."""
    t = rng.random()
    n_filas = rng.randint(5 + round(2 * t), 8 + round(2 * t))
    filas = []
    for _ in range(n_filas):
        n_pirates = rng.choices([1, 2, 3, 4], weights=[5 - 2 * t, 3, 1 + 2 * t, 3 * t])[0]
        cols = rng.sample(range(5), k=n_pirates)  # distinct columns within a round
        cuadros = [Cuadro(index=c, tipo=rng.choice([1, 2, 3]),
                          hp=1 + rng.choices([0, 1, 2, 3, 4],
                                             weights=[5 - 2 * t, 3, 1 + 2 * t, 0.2 + t, 0.3 * t])[0])
                   for c in cols]
        filas.append(Fila(cuadros=cuadros))
    # exactly one "last pirate" skin, on the final round (cosmetic, see CLAUDE.md)
    rng.choice(filas[-1].cuadros).tipo = 4
    return Level(levelNumber=0, password="", isHard=False, filas=filas)


def _random_password(rng: random.Random) -> str:
    return f"{chr(rng.randint(65, 90))}{rng.randint(0, 9999):04d}"


def _bin_of(score: float) -> int:
    for i, (lo, hi, _) in enumerate(SCORE_BINS):
        if lo <= score < hi:
            return i
    return len(SCORE_BINS) - 1


def _quotas(count: int) -> tuple[list[int], int]:
    """(champion quota per bin, search-only quota). Rounding leftovers go to
    the hardest bins."""
    n_search = max(1, round(count / ARC_SIZE)) if count >= 3 else 0
    n_champ = count - n_search
    per_bin = [int(n_champ * share) for _, _, share in SCORE_BINS]
    i = len(per_bin) - 1
    while sum(per_bin) < n_champ:
        per_bin[i] += 1
        i = i - 1 if i > 0 else len(per_bin) - 1
    return per_bin, n_search


def generate_batch(count: int, seed: int, first_number: int, used_passwords: set[str],
                   existing_signatures: set[str]) -> tuple[list[tuple[Level, dict]], dict]:
    rng = random.Random(seed)
    champion = load_policy_from_file(CURRENT_POLICY_PATH)
    bin_quota, search_quota = _quotas(count)
    bin_filled = [0] * len(SCORE_BINS)
    search_filled = 0
    accepted: list[tuple[Level, dict]] = []
    seen = set(existing_signatures)
    stats = {"attempts": 0, "reskin": 0, "bin_full": 0, "champion_lost": 0, "unwinnable": 0}
    max_attempts = count * MAX_ATTEMPTS_PER_LEVEL

    while len(accepted) < count and stats["attempts"] < max_attempts:
        stats["attempts"] += 1
        level = _draw_candidate(rng)
        sig = level.shape_signature()
        if sig in seen:
            stats["reskin"] += 1
            continue

        won = run_level(level, champion).won
        if not won and search_filled >= search_quota:
            stats["champion_lost"] += 1  # no spike slots left, skip the slow solver
            continue

        report = audit_level(level, champion, ESCALATION_BEAM_WIDTHS, DEFAULT_MAX_ROUNDS)
        if report.classification == "champion_win":
            b = _bin_of(report.difficulty_score)
            if bin_filled[b] >= bin_quota[b]:
                stats["bin_full"] += 1
                continue
            bin_filled[b] += 1
        elif report.classification == "solved_by_search_only":
            search_filled += 1
        else:
            stats["unwinnable"] += 1
            continue

        seen.add(sig)
        level.levelNumber = first_number + len(accepted)
        level.password = ensure_unique_password(_random_password(rng), used_passwords)
        used_passwords.add(level.password)
        level.isHard = (report.classification == "solved_by_search_only"
                        or report.difficulty_score >= HARD_SCORE)
        accepted.append((level, {"classification": report.classification,
                                 "difficulty_score": report.difficulty_score}))
        if len(accepted) % 10 == 0:
            print(f"  {len(accepted)}/{count} accepted after {stats['attempts']} candidates")

    stats.update({"bin_quota": bin_quota, "bin_filled": bin_filled,
                  "search_quota": search_quota, "search_filled": search_filled})
    return accepted, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a verified batch of levels locally.")
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, default=None,
                        help="random seed (default: next level number, so re-runs differ per batch)")
    parser.add_argument("--dry-run", action="store_true", help="generate and report, write nothing")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be >= 1")

    if not config.CANNONS_REPO.exists():
        # level numbers/passwords must come from the real game, never guessed
        print(f"Cannons repo not found at {config.CANNONS_REPO} (set CANNONS_REPO_PATH).")
        return 1
    last_number, used_passwords = scan_existing_levels(config.CANNONS_REPO)
    first_number = last_number + 1
    signatures = scan_existing_signatures(config.CANNONS_REPO)
    seed = args.seed if args.seed is not None else first_number
    print(f"Generating {args.count} levels from #{first_number} (seed {seed}); "
          f"{len(signatures)} existing shapes to avoid.")

    started = time.time()
    accepted, stats = generate_batch(args.count, seed, first_number, used_passwords, signatures)
    elapsed = round(time.time() - started, 1)
    print(f"Accepted {len(accepted)}/{args.count} in {elapsed}s — {stats}")

    if len(accepted) < args.count:
        # explicit, never a silent short batch: report what's missing
        print(f"WARNING: only {len(accepted)} of {args.count} could be generated within "
              f"{args.count * MAX_ATTEMPTS_PER_LEVEL} candidates. Bins/spikes short: "
              f"{stats['bin_filled']} of {stats['bin_quota']}, spikes {stats['search_filled']}/{stats['search_quota']}.")

    if args.dry_run or not accepted:
        print("Nothing written." if accepted else "Nothing to write.")
        return 0 if accepted else 1

    stamp = datetime.now().strftime("%Y%m%d")
    config.INCOMING_LEVELS_DIR.mkdir(parents=True, exist_ok=True)
    for level, _ in accepted:
        level.save(config.INCOMING_LEVELS_DIR / f"Level_{level.levelNumber}_{stamp}_batch.json")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"batch_{first_number}_{first_number + len(accepted) - 1}.json"
    report_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed, "elapsed_seconds": elapsed, "stats": stats,
        "levels": [{"levelNumber": lv.levelNumber, "password": lv.password, "isHard": lv.isHard, **info}
                   for lv, info in accepted],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(accepted)} JSON files to {config.INCOMING_LEVELS_DIR}\nReport: {report_path}")
    print("Next: import in Unity (Levels > Import Generated Levels), then "
          f"`python -m verification.extend_release --count {len(accepted)}`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

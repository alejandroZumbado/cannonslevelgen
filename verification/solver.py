"""Independent beam-search solver — answers "is this level winnable at all
under strong play", separate from whatever policy/current.py's learned
heuristic can find.

Why this exists: policy/current.py is a single greedy heuristic (whatever
the LLM last wrote and the benchmark accepted). It losing a level is
evidence THAT heuristic is weak there, not proof the level itself is broken.
This module searches much harder — a wide beam over the real, hand-verified
rules in sim/engine.py, no LLM involved — so a level that BOTH the champion
policy and this solver fail to win is much stronger evidence of an actual
design bug than the champion's result alone.

Deliberately slow-by-default and tunable via beam_width — this backs the
weekly level-audit job (verification/level_audit.py), not the 15-min
learning loop, and was explicitly asked to prefer a slower/wider search
over a fast approximate one whenever there's time budget for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sim.engine import Action, GameEngine
from sim.level import Level

# 500 official levels max out at 12 filas (verified 2026-09-10) — 60 gives
# generous headroom for mop-up rounds after the last wave without ever being
# the reason a winnable level looks lost.
DEFAULT_MAX_ROUNDS = 60

# The "how hard does it think" dial. Wider = slower + more thorough + less
# likely to miss a real winning line the heuristic underrates early (a
# narrow beam can prune away the first few moves of the only winning
# sequence before the payoff becomes visible). Timed locally against the
# real official levels before picking this default — see level_audit.py's
# module docstring for the measured numbers.
DEFAULT_BEAM_WIDTH = 800


@dataclass
class SolverResult:
    won: bool
    rounds_played: int
    nodes_expanded: int
    pirates_remaining: int = 0
    max_pirate_position_reached: int = 0  # 0..3; 3 means something broke through
    final_board_strength: int = 0         # sum of placed cannon damage at the end
    action_trace: list[Action] = field(default_factory=list)
    beam_width_used: int = 0              # which tier of solve_thoroughly() produced this result
    total_nodes_all_tiers: int = 0        # nodes_expanded summed across every tier tried, not just the last


def _heuristic(engine: GameEngine) -> float:
    """Higher is better. Used ONLY to rank/prune candidate states inside the
    beam — never affects correctness, which is entirely sim/engine.py's real
    rules. A state that has already ended in a loss is ranked last so a dead
    branch never crowds out a still-alive one; a win is handled separately
    (search stops the instant one appears in the beam, see solve()).

    Rewards low remaining threat — pirate HP weighted quadratically by how
    close it is to the loss line, since a low-HP pirate one step from the
    front is far more urgent than a high-HP pirate that just spawned — plus
    a small bonus for total placed damage (board strength), which helps the
    search prefer building up cannons even in quiet early rounds where no
    pirate is threatening yet."""
    if engine.game_ended and not engine.won:
        return float("-inf")
    threat = sum(p.hp * (p.position + 1) ** 2 for p in engine.pirates)
    board_strength = sum(c.damage for c in engine.cannons.values())
    return -threat + 0.5 * board_strength


def solve(
    level: Level,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    keep_trace: bool = False,
) -> SolverResult:
    """Beam search over sim/engine.py's exact rules. Each round, expands
    every legal action from every state currently in the beam, scores every
    resulting state with `_heuristic`, and keeps the top `beam_width`.
    Returns immediately the instant any state in the beam wins. If nothing
    ever wins by max_rounds (or every branch has independently ended in a
    loss), returns the single best-scoring state reached.

    No claim of full completeness/optimality — the true action tree is too
    large to exhaust exactly (see module docstring) — but a wide beam over
    the real rules is strictly stronger evidence than "did the current
    learned policy win", which is the whole point of running this alongside
    it in the weekly audit rather than trusting the policy alone."""
    beam: list[GameEngine] = [GameEngine(level)]
    trace: dict[int, list[Action]] = {id(beam[0]): []} if keep_trace else {}
    nodes_expanded = 0
    rounds = 0

    while rounds < max_rounds and not all(e.is_over() for e in beam):
        children: list[GameEngine] = []
        child_trace: dict[int, list[Action]] = {}

        for parent in beam:
            if parent.is_over():
                children.append(parent)
                if keep_trace:
                    child_trace[id(parent)] = trace.get(id(parent), [])
                continue
            for action in parent.valid_actions():
                child = parent.clone()
                child.play_round(action)
                nodes_expanded += 1
                children.append(child)
                if keep_trace:
                    child_trace[id(child)] = trace.get(id(parent), []) + [action]

        winners = [c for c in children if c.won]
        if winners:
            best = winners[0]
            return _result(best, nodes_expanded, won=True,
                            action_trace=child_trace.get(id(best), []) if keep_trace else [])

        children.sort(key=_heuristic, reverse=True)
        beam = children[:beam_width]
        trace = child_trace
        rounds += 1

    best = max(beam, key=_heuristic)
    return _result(best, nodes_expanded, won=best.won,
                    action_trace=trace.get(id(best), []) if keep_trace else [])



# Escalation tiers for solve_thoroughly(): cheap narrow attempt first (most
# levels either win here or are so hopeless that no width helps — see
# module docstring's "lose fast regardless of beam width" observation from
# the 2026-09-10 calibration run), escalating only when a level actually
# needs it. Widest tier chosen from real timing on the official 500-level
# campaign (see verification/level_audit.py's run notes) — cheap enough in
# aggregate to always run to completion in a weekly job, and asked
# explicitly to prefer the more thorough setting whenever the time budget
# allows it rather than stopping at "good enough".
ESCALATION_BEAM_WIDTHS = (200, 800, 3000)


def solve_thoroughly(
    level: Level,
    widths: tuple[int, ...] = ESCALATION_BEAM_WIDTHS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> SolverResult:
    """Runs solve() at each width in `widths`, ascending, stopping at the
    first win. If every width loses, returns the result from the WIDEST
    (last, most thorough) attempt — the strongest "no win found" evidence
    available — with `total_nodes_all_tiers` reflecting the full cost across
    every tier actually tried, so the audit report can show real search
    effort even when nothing ultimately won."""
    total_nodes = 0
    result: SolverResult | None = None
    for width in widths:
        result = solve(level, beam_width=width, max_rounds=max_rounds)
        total_nodes += result.nodes_expanded
        result.beam_width_used = width
        result.total_nodes_all_tiers = total_nodes
        if result.won:
            return result
    return result


def _result(engine: GameEngine, nodes_expanded: int, won: bool, action_trace: list[Action]) -> SolverResult:
    max_pos = max((p.position for p in engine.pirates), default=0)
    return SolverResult(
        won=won,
        rounds_played=engine.rounds_played,
        nodes_expanded=nodes_expanded,
        pirates_remaining=len(engine.pirates),
        max_pirate_position_reached=max_pos,
        final_board_strength=sum(c.damage for c in engine.cannons.values()),
        action_trace=action_trace,
    )

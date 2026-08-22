"""Headless simulator — reproduces the exact round timing of Cannons' real game loop.

Faithfully derived from the current source (verified against the code, not just notes):
  GameManager.cs  -> Start(), nextRound(), GameFlow1(), GameFlow2()
  CannonManagement.cs / ReceiptCannon.cs -> placement/merge rules
  Terrain/Matriz.cs + Enemys/Piratas/PirateManager.cs -> pirate advance/loss condition

Key timing fact (re-derived from GameManager.cs, do not "simplify" without re-checking
the source): a round's shoot phase (GameFlow2) fires at the state pirates were in
BEFORE that round's PirateAdvance/spawn (nextRound). Player action -> shoot -> THEN
pirates advance/next wave spawns. Getting this order wrong silently changes which
levels are "winnable".

Pirate position semantics (from Matriz.cs GetObjectInFrontOf / PirateManager.Advance):
matriz has 4 rows (0..3). A pirate spawns at position 0. Advancing checks whether the
row two steps ahead exists; if not, the game is lost. Rows 0/1/2 are the only
positions a pirate is ever actually placed at — moving off position 2 (attempting
position 3, "reaching the cannons") is what triggers GameOver. So the loss threshold
is position >= 3, not merely "reaches the last matriz row" (which is a subtlety in the
original C# two-ahead check).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from sim.level import Level, Fila

LOSS_POSITION = 3  # attempting to advance to this position ends the game
NUM_COLUMNS = 5


@dataclass
class Cannon:
    column: int
    damage: int = 1


@dataclass
class Pirate:
    column: int
    tipo: int
    hp: int
    position: int = 0


Action = tuple  # ("spawn", col) | ("move", from_col, to_col)


class GameEngine:
    """One playthrough of one Level. Deterministic given a deterministic policy."""

    def __init__(self, level: Level):
        self.level = level
        self.cannons: dict[int, Cannon] = {}
        self.pirates: list[Pirate] = []
        self.pending_spawn = Cannon(column=-1, damage=1)
        self.round = 0
        self.game_ended = False
        self.won = False
        self.rounds_played = 0
        self._next_round()  # mirrors GameManager.Start(): nextRound() then GameFlow1()

    # ---- pirate side ---------------------------------------------------

    def _pirates_advance(self) -> None:
        for pirate in self.pirates:
            if self.game_ended:
                return
            pirate.position += 1
            if pirate.position >= LOSS_POSITION:
                self.game_ended = True
                self.won = False
                return

    def _spawn_wave(self, fila: Fila) -> None:
        for cuadro in fila.cuadros:
            if cuadro.tipo >= 1:
                self.pirates.append(Pirate(column=cuadro.index, tipo=cuadro.tipo, hp=cuadro.hp))

    def _next_round(self) -> None:
        if self.game_ended:
            return
        self._pirates_advance()
        if not self.game_ended and self.round < len(self.level.filas):
            self._spawn_wave(self.level.filas[self.round])
        self.round += 1
        if not self.game_ended and self.round >= len(self.level.filas) and not self.pirates:
            self._win()

    def _win(self) -> None:
        self.game_ended = True
        self.won = True

    # ---- cannon side -----------------------------------------------------

    def valid_actions(self) -> list[Action]:
        actions: list[Action] = [("spawn", c) for c in range(NUM_COLUMNS)]
        for a in self.cannons:
            for b in range(NUM_COLUMNS):
                if b != a:
                    actions.append(("move", a, b))
        return actions

    def _place(self, cannon: Cannon, target_col: int) -> None:
        resident = self.cannons.get(target_col)
        if resident is not None:
            cannon.damage += resident.damage  # merge: absorbs resident's damage
        cannon.column = target_col
        self.cannons[target_col] = cannon

    def apply_action(self, action: Action) -> None:
        kind = action[0]
        if kind == "spawn":
            _, col = action
            self._place(self.pending_spawn, col)
            self.pending_spawn = Cannon(column=-1, damage=1)
        elif kind == "move":
            _, src, dst = action
            mover = self.cannons.pop(src)
            self._place(mover, dst)
        else:
            raise ValueError(f"unknown action {action!r}")

    def _most_advanced_pirate(self, column: int) -> Pirate | None:
        candidates = [p for p in self.pirates if p.column == column]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.position)

    def _shoot_phase(self) -> None:
        for col, cannon in self.cannons.items():
            target = self._most_advanced_pirate(col)
            if target is not None:
                target.hp -= cannon.damage
        self.pirates = [p for p in self.pirates if p.hp > 0]
        if not self.pirates and self.round >= len(self.level.filas):
            self._win()

    # ---- public API --------------------------------------------------

    def play_round(self, action: Action) -> None:
        """Apply one player action (must be one of valid_actions()), then resolve
        the shoot phase and advance to the next round — mirrors one full
        GameFlow1 -> player drop -> GameFlow2 -> nextRound cycle."""
        if self.game_ended:
            return
        self.apply_action(action)
        self.rounds_played += 1
        self._shoot_phase()
        if not self.game_ended:
            self._next_round()

    def is_over(self) -> bool:
        return self.game_ended

    def clone(self) -> "GameEngine":
        return deepcopy(self)


def run_level(level: Level, policy, max_rounds: int = 200) -> GameEngine:
    """Plays `level` to completion using `policy.choose_action(engine) -> Action`.
    Returns the finished engine (engine.won tells the outcome).

    A policy that raises (bad logic, malformed action, whatever) or returns an
    action apply_action rejects counts as an immediate loss for that level,
    not a crash — these are AI-generated policies benchmarked unattended for
    a month; a buggy candidate must score badly, not take the process down.
    This was not theoretical: a real scheduled run hit exactly this."""
    engine = GameEngine(level)
    rounds = 0
    while not engine.is_over() and rounds < max_rounds:
        try:
            action = policy.choose_action(engine)
            engine.play_round(action)
        except Exception:  # noqa: BLE001 — deliberately broad, see docstring
            engine.game_ended = True
            engine.won = False
            break
        rounds += 1
    return engine

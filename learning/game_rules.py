"""Single authoritative description of Cannons' real rules, injected into every
LLM prompt that reasons about the game (level_designer, strategy_learner).

Exists because the LLM invented a mechanic that isn't real: across several
2026-08-25/26 cycles it repeatedly proposed "tipo 3 pirates are immune to base
damage" / "tipo 3 blocks line of fire", tried to code it into the policy
(strategy_learner), and got a hypothesis "confirmed" into
knowledge/level_rules_learned.json off a single coincidental test level
(level_designer). Neither sim/engine.py nor the real Unity game (Cannons/
CLAUDE.md, Terrain/Level.cs, GameManager.cs) implement anything tipo-based
beyond "which visual skin" / "is this the level's last pirate" — `tipo` never
touches damage or blocking. This text is the fix: state the real rules
explicitly instead of leaving `Pirate.tipo: int` bare for the model to guess at.

If sim/engine.py's actual behavior ever diverges from this text, the code is
the source of truth — update this file to match it, not the other way around.
"""

GAME_RULES = """\
GAME RULES — authoritative, matches sim/engine.py and the real Unity game
exactly. Do not assume any mechanic beyond what's stated here: if a field
below isn't described as affecting something, it has NO effect on that thing.
If you're about to justify a hypothesis by what a pirate's `tipo` value does
mechanically, stop — re-read this block first.

- Grid: 5 columns (0-4). At most one cannon per column.
- Pirate fields: column, hp, tipo, position (0, 1, or 2; advancing to
  position 3 ends the game in a loss for whoever is at that position).
- `tipo` (1, 2, 3, or 4) is COSMETIC ONLY — which sprite/skin is used.
  Tipo 1-3 = normal pirates, all three IDENTICAL in stats and behavior.
  Tipo 4 just marks "this is the level's last pirate" (used for music/UI in
  the real game). No tipo value changes damage taken, damage dealt, whether a
  pirate can be targeted, or blocking. A tipo-3 pirate with HP 1 dies to one
  base-damage hit exactly like a tipo-1 pirate with HP 1 — there is no
  immunity, no armor, no special resistance tied to tipo, ever.
- Damage: a placed cannon has `damage` (1 base; merging adds the absorbed
  cannon's damage). Each round, a column's cannon (if any) hits ONLY the
  single most-advanced pirate in that column (highest `position`) — every
  other pirate in that column is untouched that round, regardless of tipo.
- Blocking: because only the most-advanced pirate in a column is ever hit, a
  pirate standing behind another pirate in the same column can't be damaged
  until the one in front dies. This is purely a POSITION effect — whichever
  pirate currently has the highest `position` in that column blocks the
  others behind it. It has nothing to do with tipo; a tipo-1 pirate in front
  blocks exactly as hard as a tipo-3 or tipo-4 pirate in front would.
- Round order (engine.py: apply_action -> shoot_phase -> next_round): player
  places/moves one cannon -> shoot phase (as above) -> pirates advance one
  position -> next wave spawns.
- Merge: placing a cannon onto an already-occupied column absorbs the
  resident cannon's damage (new damage = own + resident's) and destroys the
  resident.
- Loss: any pirate reaching position 3 ends the game immediately.
- Win: all filas (waves) have been spawned AND no pirates remain alive.

Ground every hypothesis in these rules (or in what sim/engine.py's source
actually does, if you have reason to check it) — never invent behavior for a
field this block doesn't say affects anything.
"""

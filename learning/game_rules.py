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
- Drag (move): instead of dropping the new cannon, the player may DRAG an
  already placed cannon to another column (action ["move", from, to]). It
  keeps its damage, leaves its old column empty, and merges if the target is
  occupied. The unused new cannon waits for a later round. This is how a
  player brings damage built up in one column to a threat in another.
  Worked example (needs drag — the spawn-only solver can't win it):
    R1: c2 hp1, c3 hp2 | R2: c2 hp2, c3 hp2 | R3: c2 hp2, c3 hp1 | R4: c1 hp8, c3 hp1
  Win: spawn c3, spawn c2, spawn c1 (waiting BEFORE the hp-8 arrives, so
  none of its 3 shots is wasted), spawn c1 (merge -> 2, hp 8->6), spawn c2
  (merge -> 2, hp 6->4), then move c2 -> c1 (merge -> 4, kills it). The
  dragged damage must arrive on time: an LLM that left column 1 empty and
  dragged a damage-3 cannon over only on the hp-8's 2nd shot lost (3+3 < 8).
  Pre-place in the threatened column, then drag for the final blow.
- LIMITS (measured with the solver, 2026-10-14) — how tall a pirate can be
  and still be killable. Killing power in a column = the damage present on
  each of the pirate's 3 shots; cannons stacked there BEFORE it spawns count.
  A pirate that spawns in round r (r=1 = present before the first move):
    alone (nothing else to kill): max HP = 3r+3 -> r1: 6, r2: 9, r3+: 10 (the HP cap).
    with 2 HP-1 pirates per round on other columns: r1: 6, r2: 4, r3: 6, r4: 9, r5+: 10.
  Every other pirate competes for the same cannons; that competition is what
  makes a level hard. Dragging lets one cannon serve two columns (kill the
  small ones, then move over for the tall one) — the worked example above puts
  an HP-8 in round 4 under pressure and is still winnable only thanks to it.
  "Extreme but winnable" = sit right at these limits: one HP more and no line
  of play wins.
- Loss: any pirate reaching position 3 ends the game immediately.
- Win: all filas (waves) have been spawned AND no pirates remain alive.
- Shot budget is FIXED at 3 per pirate, period — NOT "however many filas the
  level has left". Every pirate advances one position every single round from
  the moment it spawns, whether or not it took damage that round (position
  advance is unconditional, not tied to being shot at). Position 3 = loss.
  So an unblocked pirate always gets exactly 3 shots (the shoot phase in the
  round it spawns, +1, +2) before it must be dead or the game is over — this
  number never grows just because the level has more filas after it, or
  because there were empty filas before it, or because of anything else.
  Concretely: a level with 500 filas gives a lone HP-5 pirate exactly the
  same 3 shots as a level with 5 filas — it is UNBEATABLE without a merge in
  both. If you're about to justify a hypothesis with "there are N filas left,
  so it can absorb N hits" — stop, that reasoning is wrong; the cap is always
  exactly 3, full stop, never "however many filas remain".
  A blocked pirate's own 3-shot clock still runs while it's blocked (it's
  advancing every round like every other pirate) — being blocked costs it
  shots, it never grants extra time.
- The game does NOT end just because every fila has been spawned. Spawning
  stops (no new pirate appears), but rounds keep being played — pirates keep
  advancing, keep getting shot at — until every remaining pirate is dead
  (win) or one reaches position 3 (loss). So "total filas in the level" or
  "filas remaining before/after a pirate's own spawn" is NEVER what bounds
  whether that pirate can be killed — verified directly: a pirate spawned on
  a level's very LAST fila (officially zero filas left) still gets its full,
  ordinary 3 shots, because the game simply keeps running rounds with no new
  spawns until it's resolved. The only two things that ever matter for a
  single pirate are its HP (vs. the fixed 3-shot cap, or more if a blocker in
  front ate some of those 3 rounds first) and whether merges raised the
  cannon's damage — never any fila/round count in the level.

Ground every hypothesis in these rules (or in what sim/engine.py's source
actually does, if you have reason to check it) — never invent behavior for a
field this block doesn't say affects anything.
"""

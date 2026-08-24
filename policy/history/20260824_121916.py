NUM_COLUMNS = 5
MAX_POSITION = 3  # reaching this loses the game


class Policy:
    name = "column_deficit_v1"

    # ------------------------------------------------------------------ #
    def choose_action(self, engine):
        # 1️⃣  Gather pirates per column, sorted by position descending
        col_pirates = {c: [] for c in range(NUM_COLUMNS)}
        for p in engine.pirates:
            col_pirates[p.column].append(p)
        for lst in col_pirates.values():
            lst.sort(key=lambda pp: pp.position, reverse=True)

        # 2️⃣  Compute deficit and urgency for every column that has pirates
        deficits = []          # list of (deficit, urgency, column)
        for col, pirates in col_pirates.items():
            if not pirates:
                continue

            total_hp = sum(p.hp for p in pirates)
            top_pos = min(p.position for p in pirates)          # closest to loss
            rounds_left = MAX_POSITION - top_pos                # rounds before loss

            cannon = engine.cannons.get(col)
            dmg = cannon.damage if cannon else 0
            possible_damage = dmg * rounds_left

            deficit = total_hp - possible_damage
            if deficit > 0:
                # urgency = how soon the front pirate would reach the loss row
                front_pos = pirates[0].position
                time_to_loss = MAX_POSITION - front_pos
                deficits.append((deficit, time_to_loss, col))

        # 3️⃣  If any column needs more damage, handle the most urgent one
        if deficits:
            # pick column with largest deficit, break ties by earliest time_to_loss,
            # then by lowest column index for determinism
            deficits.sort(key=lambda x: (-x[0], x[1], x[2]))
            _, _, target = deficits[0]

            # If the target column has no cannon yet → spawn there
            if target not in engine.cannons:
                return ("spawn", target)

            # Otherwise try to merge the weakest donor into the target
            donor = self._weakest_cannon_excluding(engine.cannons, exclude=target)
            if donor is not None:
                return ("move", donor, target)

            # No donor available (should not happen often) → just spawn (will merge next round)
            return ("spawn", target)

        # 4️⃣  No immediate deficit → growth / housekeeping phase
        # a) Prefer spawning on an empty column that already hosts a pirate
        empty_with_pirate = [
            c for c in range(NUM_COLUMNS)
            if c not in engine.cannons and col_pirates[c]
        ]
        if empty_with_pirate:
            # choose the column whose front pirate is furthest forward
            chosen = max(empty_with_pirate,
                         key=lambda c: col_pirates[c][0].position)
            return ("spawn", chosen)

        # b) Otherwise, spawn on the column with the smallest damage that still has pirates
        cols_with_cannon = [c for c in engine.cannons if col_pirates[c]]
        if cols_with_cannon:
            weakest = min(cols_with_cannon,
                          key=lambda c: (engine.cannons[c].damage, c))
            return ("spawn", weakest)

        # c) All columns occupied and no pirates left → merge weakest into strongest
        if engine.cannons:
            weakest = min(engine.cannons.items(),
                          key=lambda kv: (kv[1].damage, kv[0]))[0]
            # target = column with highest damage (or most threatening front pirate)
            target = max(engine.cannons.items(),
                         key=lambda kv: (kv[1].damage, kv[0]))[0]
            if weakest != target:
                return ("move", weakest, target)
            # fallback: spawn on weakest (self‑merge)
            return ("spawn", weakest)

        # d) No cannons at all (unlikely) – just spawn in first column
        return ("spawn", 0)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _weakest_cannon_excluding(cannons, exclude):
        """Return column of the weakest cannon not equal to *exclude*,
        or None if none exists."""
        candidates = [(col, c.damage) for col, c in cannons.items()
                      if col != exclude]
        if not candidates:
            return None
        # deterministic: weakest damage, then lowest column
        return min(candidates, key=lambda kv: (kv[1], kv[0]))[0]
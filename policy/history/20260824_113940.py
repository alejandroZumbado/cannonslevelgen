NUM_COLUMNS = 5
MAX_POSITION = 3  # reaching this loses the game


class Policy:
    name = "danger_first_merge_v1_fixed"

    def choose_action(self, engine):
        # 1️⃣  Find the most advanced pirate per column
        front = self._front_pirates(engine)

        # 2️⃣  Look for a column where the pirate will survive the remaining rounds
        for col, pirate in front.items():
            if pirate is None:
                continue
            cannon = engine.cannons.get(col)
            rounds_left = MAX_POSITION - pirate.position

            # ---- NEW URGENT SPAWN CHECK ----
            # No cannon yet, but even base damage (1 per round) cannot kill the pirate
            if cannon is None:
                if pirate.hp > rounds_left:          # cannot survive without a cannon
                    return ("spawn", col)            # spawn now (or merge next round)
                # otherwise not urgent – keep looking
                continue
            # --------------------------------

            # Existing cannon – see if it can kill the front pirate in time
            dmg = cannon.damage
            if dmg * rounds_left < pirate.hp:
                # Need more damage now → try to merge another cannon into this column
                src = self._choose_donor(engine.cannons, exclude=col)
                if src is not None:
                    return ("move", src, col)
                # No donor available, fall back to spawning (will merge next round)
                break

        # 3️⃣  No urgent merges – decide where to spawn
        # Prefer empty columns that already have a threatening pirate
        empty_threat = [
            c for c in range(NUM_COLUMNS)
            if c not in engine.cannons and front.get(c) is not None
        ]
        if empty_threat:
            # choose the column with the furthest pirate (largest position)
            col = max(empty_threat, key=lambda c: front[c].position)
            return ("spawn", col)

        # Next, columns with a cannon that is still undersized for its front pirate
        undersized = []
        for col, cannon in engine.cannons.items():
            pirate = front.get(col)
            if pirate is None:
                continue
            needed = pirate.hp
            if cannon.damage < needed:
                undersized.append(col)
        if undersized:
            col = max(undersized, key=lambda c: front[c].position)
            return ("spawn", col)

        # If there are still empty columns, just fill the first one
        empty = [c for c in range(NUM_COLUMNS) if c not in engine.cannons]
        if empty:
            return ("spawn", empty[0])

        # All columns occupied and no urgent need – merge the weakest into the
        # column with the most threatening pirate to keep damage growing.
        weakest = min(engine.cannons.items(), key=lambda kv: kv[1].damage)[0]
        # pick a target column (the one with highest front pirate position)
        target = max(
            (c for c in engine.cannons if front.get(c) is not None),
            key=lambda c: front[c].position,
            default=weakest,
        )
        if weakest != target:
            return ("move", weakest, target)

        # Fallback: spawn on the weakest column (will merge with itself)
        return ("spawn", weakest)

    # --------------------------------------------------------------------- #
    @staticmethod
    def _front_pirates(engine):
        """Return dict column → most‑advanced Pirate (or None)."""
        front = {c: None for c in range(NUM_COLUMNS)}
        for p in engine.pirates:
            cur = front[p.column]
            if cur is None or p.position > cur.position:
                front[p.column] = p
        return front

    @staticmethod
    def _choose_donor(cannons, exclude):
        """Return column of the weakest cannon not equal to *exclude*,
        or None if no such cannon exists."""
        candidates = [(col, c.damage) for col, c in cannons.items() if col != exclude]
        if not candidates:
            return None
        # deterministic: pick the weakest, tie‑break by lowest column
        donor = min(candidates, key=lambda kv: (kv[1], kv[0]))[0]
        return donor
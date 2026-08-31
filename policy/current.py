NUM_COLUMNS = 5
MAX_POSITION = 3          # reaching this loses the game


class Policy:
    name = "deadline_deficit_heuristic"

    # ------------------------------------------------------------------ #
    def choose_action(self, engine):
        # ----- snapshot of current state -----
        cannons = {c: engine.cannons[c].damage for c in engine.cannons}
        pirates = [
            {"column": p.column, "position": p.position, "hp": p.hp}
            for p in engine.pirates
        ]

        # ----- helpers -------------------------------------------------
        def apply_action(cann_map, act):
            """Return a new cannon map after performing act (spawn or move)."""
            new = dict(cann_map)
            if act[0] == "spawn":
                col = act[1]
                new[col] = new.get(col, 0) + 1          # base damage of new cannon
            else:  # move
                donor, target = act[1], act[2]
                dmg = new.pop(donor)                     # donor disappears
                new[target] = new.get(target, 0) + dmg   # damage stacks
                # the pending spawn cannon is lost – nothing else to do
            return new

        def simulate_round(cann_map, pir_list):
            """One full round: damage then advance. Returns (pirates, loss)."""
            pir = [dict(p) for p in pir_list]

            # damage – only the front pirate in each column is hit
            for col, dmg in cann_map.items():
                if dmg <= 0:
                    continue
                col_p = [p for p in pir if p["column"] == col]
                if not col_p:
                    continue
                front = max(col_p, key=lambda p: p["position"])
                front["hp"] -= dmg
                if front["hp"] <= 0:
                    pir.remove(front)

            # advance
            for p in pir:
                p["position"] += 1

            loss = any(p["position"] >= MAX_POSITION for p in pir)
            return pir, loss

        def weighted_deficit(cann_map, pir_list):
            """
            Sum over all pirates of the HP that cannot be dealt before they
            would reach position 3, assuming the current cannon damage stays
            constant for the remaining rounds of that pirate.
            """
            total = 0
            for p in pir_list:
                steps = MAX_POSITION - p["position"]          # rounds left before loss
                dmg = cann_map.get(p["column"], 0)
                # total damage we could inflict before it reaches position 3
                possible = dmg * steps
                need = max(0, p["hp"] - possible)
                total += need
            return total

        def max_position(pir_list):
            return max((p["position"] for p in pir_list), default=-1)

        # ----- enumerate all legal actions -----
        actions = []

        # spawn actions (including merges)
        for col in range(NUM_COLUMNS):
            actions.append(("spawn", col))

        # move actions (donor != target, donor must have a cannon)
        for donor in cannons:
            for target in range(NUM_COLUMNS):
                if donor == target:
                    continue
                actions.append(("move", donor, target))

        best_action = None
        best_key = None   # tuple used for comparison (lower is better)

        for act in actions:
            # ----- apply the chosen action (before round) -----
            new_cann = apply_action(cannons, act)

            # ----- simulate this round -----
            post_pir, loss = simulate_round(new_cann, pirates)
            if loss:
                continue          # unsafe action, discard

            # ----- evaluation metrics -----
            deficit = weighted_deficit(new_cann, post_pir)
            maxpos = max_position(post_pir)
            total_cannon_damage = sum(new_cann.values())

            # Primary ordering:
            #   1. smallest deficit (hardest to survive)
            #   2. smallest max pirate position (keep them back)
            #   3. largest total cannon damage (prefer merges that build power)
            #   4. prefer spawn over move, then lower column numbers (deterministic)
            tie = (0 if act[0] == "spawn" else 1, act)
            key = (deficit, maxpos, -total_cannon_damage, tie)

            if best_key is None or key < best_key:
                best_key = key
                best_action = act

        # ----- fallback (should never happen) -----
        if best_action is None:
            for col in range(NUM_COLUMNS):
                if col not in engine.cannons:
                    return ("spawn", col)
            return ("spawn", 0)

        return best_action
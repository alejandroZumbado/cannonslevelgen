NUM_COLUMNS = 5
MAX_POSITION = 3  # reaching this loses the game


class Policy:
    name = "two_round_lookahead_urgency_fixed"

    # ------------------------------------------------------------------ #
    def choose_action(self, engine):
        # ---------- snapshot ----------
        cannons = {c: engine.cannons[c].damage for c in engine.cannons}
        pirates = [
            {"column": p.column, "position": p.position, "hp": p.hp}
            for p in engine.pirates
        ]

        # ---------- helpers ----------
        def simulate_round(cannons_map, pirates_list):
            """One full round: damage then advance."""
            cann = dict(cannons_map)
            pir = [dict(p) for p in pirates_list]

            # damage phase – only front pirate per column
            for col, dmg in cann.items():
                if dmg <= 0:
                    continue
                col_p = [p for p in pir if p["column"] == col]
                if not col_p:
                    continue
                front = max(col_p, key=lambda p: p["position"])
                front["hp"] -= dmg
                if front["hp"] <= 0:
                    pir.remove(front)

            # advance phase
            for p in pir:
                p["position"] += 1

            # loss check
            loss = any(p["position"] >= MAX_POSITION for p in pir)
            return cann, pir, loss

        def possible_damage(cannon_dmg, col_pirates):
            """Total damage that can be dealt before any pirate reaches loss,
               assuming cannon damage stays constant."""
            pirates = [dict(p) for p in col_pirates]
            total = 0
            while pirates:
                front = max(pirates, key=lambda p: p["position"])
                front["hp"] -= cannon_dmg
                total += cannon_dmg
                if front["hp"] <= 0:
                    pirates.remove(front)
                for p in pirates:
                    p["position"] += 1
                if any(p["position"] >= MAX_POSITION for p in pirates):
                    break
            return total

        def total_deficit(cannons_map, pirates_list):
            """Sum of HP that cannot be eliminated before loss with current dmg."""
            deficit = 0
            cols = {c: [] for c in range(NUM_COLUMNS)}
            for p in pirates_list:
                cols[p["column"]].append(p)

            for col, plist in cols.items():
                if not plist:
                    continue
                dmg = cannons_map.get(col, 0)
                possible = possible_damage(dmg, plist)
                total_hp = sum(p["hp"] for p in plist)
                if total_hp > possible:
                    deficit += total_hp - possible
            return deficit

        def evaluate_state(cannons_map, pirates_list):
            """(deficit, max_position) for a given state."""
            deficit = total_deficit(cannons_map, pirates_list)
            max_pos = max((p["position"] for p in pirates_list), default=-1)
            return deficit, max_pos

        # ---------- enumerate legal actions ----------
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
        best_key = None  # tuple used for comparison

        # ---------- evaluate each action ----------
        for act in actions:
            # apply action to cannon layout
            new_cannons = dict(cannons)

            if act[0] == "spawn":
                col = act[1]
                new_cannons[col] = new_cannons.get(col, 0) + 1
            else:  # move
                donor, target = act[1], act[2]
                dmg = new_cannons.pop(donor)
                new_cannons[target] = new_cannons.get(target, 0) + dmg
                # pending spawn cannon is lost this round – no extra effect

            # ----- first round simulation -----
            post_cannons, post_pirates, loss = simulate_round(new_cannons, pirates)
            if loss:
                continue  # unsafe action

            deficit1, maxpos1 = evaluate_state(post_cannons, post_pirates)

            # ----- second‑round optimistic look‑ahead (best possible spawn) -----
            best_def2 = None
            best_max2 = None

            for col in range(NUM_COLUMNS):
                # spawn in col for the next round
                c2 = dict(post_cannons)
                c2[col] = c2.get(col, 0) + 1
                c2_after, p2_after, loss2 = simulate_round(c2, post_pirates)
                if loss2:
                    continue
                d2, m2 = evaluate_state(c2_after, p2_after)
                if (best_def2 is None) or (d2 < best_def2) or (d2 == best_def2 and m2 < best_max2):
                    best_def2 = d2
                    best_max2 = m2

            # if every spawn leads to loss, treat second round as very bad
            if best_def2 is None:
                best_def2 = 10 ** 9
                best_max2 = 10 ** 9

            # tie‑breaker: prefer spawn over move when everything else equal
            tie_breaker = 0 if act[0] == "spawn" else 1

            # comparison key: lower is better
            key = (deficit1, maxpos1, best_def2, best_max2, tie_breaker, act)

            if (best_key is None) or (key < best_key):
                best_key = key
                best_action = act

            # early exit: perfect state (no deficit now and after next spawn, and no urgent pirates)
            if deficit1 == 0 and maxpos1 <= 1 and best_def2 == 0 and best_max2 <= 1:
                break

        # fallback – should never happen, but keep deterministic
        if best_action is None:
            for col in range(NUM_COLUMNS):
                if col not in engine.cannons:
                    return ("spawn", col)
            return ("spawn", 0)

        return best_action
NUM_COLUMNS = 5
MAX_POSITION = 3  # reaching this loses the game


class Policy:
    name = "lookahead_merge_v2_tie_urgency"

    # ------------------------------------------------------------------ #
    def choose_action(self, engine):
        # Gather current state
        cannons = {c: engine.cannons[c].damage for c in engine.cannons}
        pirates = [
            {"column": p.column, "position": p.position, "hp": p.hp}
            for p in engine.pirates
        ]

        # ------------------------------------------------------------------
        # Helper: simulate one full round (damage + advance) for a given
        #         cannon layout and pirate list.
        def simulate_round(cannons_map, pirates_list):
            # copy mutable structures
            cannons = dict(cannons_map)
            pirates = [dict(p) for p in pirates_list]

            # 1) damage phase – each column hits its front pirate
            for col, dmg in cannons.items():
                if dmg <= 0:
                    continue
                # pirates in this column
                col_p = [p for p in pirates if p["column"] == col]
                if not col_p:
                    continue
                # front pirate = highest position (closest to loss)
                front = max(col_p, key=lambda p: p["position"])
                front["hp"] -= dmg
                if front["hp"] <= 0:
                    pirates.remove(front)

            # 2) advance phase – all surviving pirates move forward
            for p in pirates:
                p["position"] += 1

            # 3) check immediate loss
            loss = any(p["position"] >= MAX_POSITION for p in pirates)
            return cannons, pirates, loss

        # ------------------------------------------------------------------
        # Helper: compute how much total damage can still be dealt in a column
        #         before a pirate would reach the loss row, assuming the
        #         current cannon damage stays constant.
        def possible_damage(cannon_dmg, col_pirates):
            # make copies
            pirates = [dict(p) for p in col_pirates]
            total = 0
            while pirates:
                # front pirate
                front = max(pirates, key=lambda p: p["position"])
                front["hp"] -= cannon_dmg
                total += cannon_dmg
                if front["hp"] <= 0:
                    pirates.remove(front)
                # advance all
                for p in pirates:
                    p["position"] += 1
                # stop if any would reach loss
                if any(p["position"] >= MAX_POSITION for p in pirates):
                    break
            return total

        # ------------------------------------------------------------------
        # Compute total deficit for a given state (after a round)
        def total_deficit(cannons_map, pirates_list):
            deficit = 0
            # group pirates by column
            col_groups = {c: [] for c in range(NUM_COLUMNS)}
            for p in pirates_list:
                col_groups[p["column"]].append(p)

            for col, plist in col_groups.items():
                if not plist:
                    continue
                total_hp = sum(p["hp"] for p in plist)
                dmg = cannons_map.get(col, 0)
                possible = possible_damage(dmg, plist)
                if total_hp > possible:
                    deficit += total_hp - possible
            return deficit

        # ------------------------------------------------------------------
        # Enumerate all legal actions
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
        best_score = None          # lower is better (total deficit)
        best_max_pos = None        # lower is better (max pirate position after round)

        # deterministic tie‑break by action tuple order as final fallback
        for act in actions:
            # ----- apply action to get new cannon layout -----
            new_cannons = dict(cannons)

            if act[0] == "spawn":
                col = act[1]
                new_cannons[col] = new_cannons.get(col, 0) + 1
            else:  # move
                donor, target = act[1], act[2]
                dmg = new_cannons.pop(donor)
                new_cannons[target] = new_cannons.get(target, 0) + dmg
                # pending spawn cannon is lost this round – no extra effect

            # ----- simulate one round -----
            post_cannons, post_pirates, loss = simulate_round(new_cannons, pirates)
            if loss:
                # action leads to immediate defeat – discard
                continue

            # ----- evaluate remaining deficit -----
            score = total_deficit(post_cannons, post_pirates)

            # compute urgency metric: highest pirate position after this round
            if post_pirates:
                max_pos = max(p["position"] for p in post_pirates)
            else:
                max_pos = -1   # no pirates left -> best possible

            # ----- select best action -----
            better = False
            if best_score is None or score < best_score:
                better = True
            elif score == best_score:
                # secondary tie‑breaker: lower max pirate position is preferred
                if max_pos < best_max_pos:
                    better = True
                elif max_pos == best_max_pos and act < best_action:
                    # final deterministic lexical fallback
                    better = True

            if better:
                best_score = score
                best_action = act
                best_max_pos = max_pos

            # early exit: perfect state (no deficit and no pirates ahead)
            if best_score == 0 and best_max_pos <= 1:
                break

        # fallback if no safe action (should be rare)
        if best_action is None:
            for col in range(NUM_COLUMNS):
                if col not in engine.cannons:
                    return ("spawn", col)
            return ("spawn", 0)

        return best_action
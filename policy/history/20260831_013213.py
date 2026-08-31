NUM_COLUMNS = 5
MAX_POSITION = 3          # reaching this loses the game


class Policy:
    name = "kill_pos2_first_with_lookahead"

    # ------------------------------------------------------------------ #
    def choose_action(self, engine):
        # ----- snapshot of current state -----
        cannons = {c: engine.cannons[c].damage for c in engine.cannons}
        pirates = [
            {"column": p.column, "position": p.position, "hp": p.hp}
            for p in engine.pirates
        ]

        # ----- core simulation helpers -----
        def simulate_round(cann_map, pir_list):
            """Apply one full round: damage then advance.
               Returns (new_cannons, new_pirates, loss_flag)."""
            cann = dict(cann_map)
            pir = [dict(p) for p in pir_list]

            # damage – only the front pirate in each column is hit
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

            # advance
            for p in pir:
                p["position"] += 1

            # loss check
            loss = any(p["position"] >= MAX_POSITION for p in pir)
            return cann, pir, loss

        def total_deficit(cann_map, pir_list):
            """HP that cannot be removed before any pirate reaches loss,
               assuming current cannon damages stay constant."""
            deficit = 0
            cols = {c: [] for c in range(NUM_COLUMNS)}
            for p in pir_list:
                cols[p["column"]].append(p)

            for col, plist in cols.items():
                if not plist:
                    continue
                dmg = cann_map.get(col, 0)
                col_pirates = [dict(p) for p in plist]
                total_hp = sum(p["hp"] for p in col_pirates)
                possible = 0
                while col_pirates:
                    front = max(col_pirates, key=lambda p: p["position"])
                    front["hp"] -= dmg
                    possible += dmg
                    if front["hp"] <= 0:
                        col_pirates.remove(front)
                    for p in col_pirates:
                        p["position"] += 1
                    if any(p["position"] >= MAX_POSITION for p in col_pirates):
                        break
                if total_hp > possible:
                    deficit += total_hp - possible
            return deficit

        def max_position(pir_list):
            return max((p["position"] for p in pir_list), default=-1)

        # ----- future look‑ahead (same as champion, depth 2) ----- #
        def future_deficit(cann_map, pir_list, rounds_left):
            """Best possible deficit after `rounds_left` future spawn rounds."""
            if rounds_left == 0:
                return total_deficit(cann_map, pir_list)

            best = None
            for col in range(NUM_COLUMNS):
                nxt_cann = dict(cann_map)
                nxt_cann[col] = nxt_cann.get(col, 0) + 1
                nxt_cann, nxt_pir, loss = simulate_round(nxt_cann, pir_list)
                if loss:
                    continue
                val = future_deficit(nxt_cann, nxt_pir, rounds_left - 1)
                if best is None or val < best:
                    best = val
            return best if best is not None else 10 ** 9

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
            new_cann = dict(cannons)

            if act[0] == "spawn":
                col = act[1]
                new_cann[col] = new_cann.get(col, 0) + 1
            else:   # move
                donor, target = act[1], act[2]
                dmg = new_cann.pop(donor)
                new_cann[target] = new_cann.get(target, 0) + dmg
                # pending spawn cannon is lost this round (no extra effect)

            # ----- damage phase with kill‑pos2 detection -----
            killed_pos2 = False
            # copy pirates for this simulation
            pir = [dict(p) for p in pirates]
            for col, dmg in new_cann.items():
                if dmg <= 0:
                    continue
                col_p = [p for p in pir if p["column"] == col]
                if not col_p:
                    continue
                front = max(col_p, key=lambda p: p["position"])
                if front["position"] == 2 and dmg >= front["hp"]:
                    killed_pos2 = True
                front["hp"] -= dmg
                if front["hp"] <= 0:
                    pir.remove(front)

            # advance pirates
            for p in pir:
                p["position"] += 1

            loss = any(p["position"] >= MAX_POSITION for p in pir)
            if loss:
                continue          # unsafe action, discard

            post_cann, post_pir, _ = simulate_round(new_cann, pirates)  # reuse for consistency
            # (the above simulate_round repeats the same damage/advance; we keep it for later metrics)

            # ----- evaluation metrics -----
            primary = 0 if killed_pos2 else 1                     # kill pos‑2 pirates first
            deficit_now = total_deficit(post_cann, post_pir)
            maxpos_now = max_position(post_pir)
            future_def = future_deficit(post_cann, post_pir, rounds_left=2)

            # tie‑breaker: prefer spawn over move, then lower column numbers
            tie = (0 if act[0] == "spawn" else 1, act)

            key = (primary, deficit_now, maxpos_now, future_def, tie)

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
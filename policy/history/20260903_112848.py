NUM_COLUMNS = 5
MAX_POSITION = 3          # reaching this loses the game


class Policy:
    name = "kill_priority_two_step_lookahead"

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
                # pending spawn cannon is lost – nothing else to do
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

        def deficit(cann_map, pir_list):
            """Total HP that cannot be dealt before pirates reach position 3."""
            total = 0
            for p in pir_list:
                steps = MAX_POSITION - p["position"]
                dmg = cann_map.get(p["column"], 0)
                possible = dmg * steps
                need = max(0, p["hp"] - possible)
                total += need
            return total

        def kills_lethal(act, cann_map, pir_list):
            """True if this action kills a pirate that is currently on position 2."""
            # column that will fire this round
            col = act[1] if act[0] == "spawn" else act[2]
            dmg = cann_map.get(col, 0)
            if dmg <= 0:
                return False
            col_pirates = [p for p in pir_list if p["column"] == col]
            if not col_pirates:
                return False
            front = max(col_pirates, key=lambda p: p["position"])
            return front["position"] == 2 and front["hp"] <= dmg

        def kills_any(act, cann_map, pir_list):
            """True if this action kills the front pirate in its column (any position)."""
            col = act[1] if act[0] == "spawn" else act[2]
            dmg = cann_map.get(col, 0)
            if dmg <= 0:
                return False
            col_pirates = [p for p in pir_list if p["column"] == col]
            if not col_pirates:
                return False
            front = max(col_pirates, key=lambda p: p["position"])
            return front["hp"] <= dmg

        # ----- enumerate all legal first actions -----
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
            # ----- first round simulation -----
            cann_after_first = apply_action(cannons, act)
            pirates_after_first, loss1 = simulate_round(cann_after_first, pirates)
            if loss1:
                continue          # unsafe first action

            # ----- second round: try every possible spawn **or move** -----
            second_deficit = None
            second_candidates = [("spawn", col) for col in range(NUM_COLUMNS)]
            for donor in cann_after_first:
                for target in range(NUM_COLUMNS):
                    if donor == target:
                        continue
                    second_candidates.append(("move", donor, target))

            for second_act in second_candidates:
                cann_after_second = apply_action(cann_after_first, second_act)
                pirates_after_second, loss2 = simulate_round(cann_after_second, pirates_after_first)
                if loss2:
                    continue
                d = deficit(cann_after_second, pirates_after_second)
                if second_deficit is None or d < second_deficit:
                    second_deficit = d

            if second_deficit is None:
                second_deficit = 10 ** 9   # hopeless

            # ----- evaluation key -----
            first_def = deficit(cann_after_first, pirates_after_first)
            total_damage = sum(cann_after_first.values())
            kill_lethal = 1 if kills_lethal(act, cann_after_first, pirates) else 0
            kill_any = 1 if kills_any(act, cann_after_first, pirates) else 0

            # Prefer moves over spawns when everything else ties
            tie_pref = 0 if act[0] == "move" else 1

            # key order:
            # 1. second‑round deficit (lower is better)
            # 2. first‑round deficit
            # 3. total cannon damage (higher is better)
            # 4. kill‑lethal bonus (higher is better)
            # 5. kill‑any bonus (higher is better)
            # 6. prefer move over spawn
            # 7. deterministic action tuple
            key = (
                second_deficit,
                first_def,
                -total_damage,
                -kill_lethal,
                -kill_any,
                tie_pref,
                act,
            )

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
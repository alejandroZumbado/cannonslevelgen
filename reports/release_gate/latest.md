# Release gate — GO

_2026-10-01T00:09:48+00:00_

## Warnings
- [levels] 44 release levels fail the pacing gate (breathers are expected to), e.g. [5, 8, 127, 169, 29, 43, 71, 295]
- [batches] 197 of 200 already-deployed positions changed level (first: position 2) — players' saved progress would point elsewhere (allowed only because the game isn't published)
- [live] live WebGL built 2026-09-23 09:19 but levels changed 2026-09-27 02:46 — site serves the old campaign
- [forecast] reserve has 58 pacing-ok levels; at 3.5/week from the daily bot the next batch of 100 is ~12 weeks away — use production.batch_generator for batches

## levels
```json
{
 "count": 500,
 "classifications": {
  "champion_win": 467,
  "solved_by_search_only": 33
 },
 "pacing_failures": 44
}
```

## batches
```json
{
 "deployed": 200,
 "current": 500,
 "new": 300,
 "moved_positions": 197,
 "deployed_at": "2026-09-23T09:19:49-06:00",
 "snapshot_derived": true
}
```

## live
```json
null
```

## build
```json
{
 "built_at": "2026-09-23T09:19:49-06:00",
 "levels_changed_at": "2026-09-27T02:46:05+00:00",
 "stale": true
}
```

## pipeline
```json
null
```

## forecast
```json
{
 "daily_levels_last_days": 7,
 "days": 14,
 "expected_next_week": 3.5,
 "reserve_levels": 91,
 "reserve_pacing_ok": 58,
 "weeks_to_next_batch_from_daily_bot": 12.0
}
```

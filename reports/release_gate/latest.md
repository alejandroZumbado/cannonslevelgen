# Release gate — HOLD

_2026-10-02T03:08:39+00:00_

## HOLD
- [batches] nothing new to ship since the last deploy

## Warnings
- [levels] 44 release levels fail the pacing gate (breathers are expected to), e.g. [5, 8, 127, 169, 29, 43, 71, 295]
- [live] live WebGL built 2026-09-30 20:31 but levels changed 2026-10-01 21:05 — site serves the old campaign
- [forecast] reserve has 59 pacing-ok levels; at 3.5/week from the daily bot the next batch of 100 is ~12 weeks away — use production.batch_generator for batches

## levels
```json
{
 "count": 500,
 "classifications": {
  "champion_win": 466,
  "solved_by_search_only": 34
 },
 "pacing_failures": 44
}
```

## batches
```json
{
 "deployed": 500,
 "current": 500,
 "new": 0,
 "moved_positions": 0,
 "deployed_at": "2026-10-01T02:31:19+00:00",
 "snapshot_derived": false
}
```

## live
```json
null
```

## build
```json
{
 "built_at": "2026-09-30T20:31:08-06:00",
 "levels_changed_at": "2026-10-01T21:05:24-06:00",
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
 "reserve_levels": 92,
 "reserve_pacing_ok": 59,
 "weeks_to_next_batch_from_daily_bot": 11.7
}
```

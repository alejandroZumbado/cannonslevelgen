# Release gate — GO

_2026-10-19T03:15:03+00:00_

## Warnings
- [levels] 32 release levels fail the pacing gate (breathers are expected to), e.g. [155, 113, 407, 68, 9, 99, 477, 4]
- [batches] 491 of 500 already-deployed positions changed level (first: position 2) — players' saved progress would point elsewhere (allowed only because the game isn't published)
- [live] live WebGL built 2026-10-01 21:11 but levels changed 2026-10-08 10:06 — site serves the old campaign

## levels
```json
{
 "count": 500,
 "classifications": {
  "champion_win": 466,
  "solved_by_search_only": 34
 },
 "pacing_failures": 32
}
```

## batches
```json
{
 "deployed": 500,
 "current": 500,
 "new": 0,
 "moved_positions": 491,
 "deployed_at": "2026-10-02T03:11:37+00:00",
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
 "built_at": "2026-10-01T21:11:26-06:00",
 "levels_changed_at": "2026-10-08T10:06:36-06:00",
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
 "daily_levels_last_days": 14,
 "days": 14,
 "expected_next_week": 7.0,
 "reserve_levels": 105,
 "reserve_pacing_ok": 104,
 "weeks_to_next_batch_from_daily_bot": 0.0
}
```

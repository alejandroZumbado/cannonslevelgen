# Release gate — GO

_2026-10-20T22:12:42+00:00_

## Warnings
- [batches] 491 of 500 already-deployed positions changed level (first: position 2) — players' saved progress would point elsewhere (allowed only because the game isn't published)
- [live] live WebGL built 2026-10-01 21:11 but levels changed 2026-10-19 21:06 — site serves the old campaign

## levels
```json
{
 "count": 500,
 "classifications": {
  "champion_win": 462,
  "solved_by_search_only": 38
 },
 "pacing_failures": 0
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
 "levels_changed_at": "2026-10-19T21:06:16-06:00",
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
 "daily_levels_last_days": 19,
 "days": 14,
 "expected_next_week": 9.5,
 "reserve_levels": 111,
 "reserve_pacing_ok": 110,
 "weeks_to_next_batch_from_daily_bot": 0.0
}
```

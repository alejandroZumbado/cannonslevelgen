# Release gate — GO

_2026-10-20T02:51:18+00:00_

## Warnings
- [batches] 491 of 500 already-deployed positions changed level (first: position 2) — players' saved progress would point elsewhere (allowed only because the game isn't published)
- [live] live WebGL built 2026-10-01 21:11 but levels changed 2026-10-18 21:19 — site serves the old campaign

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
 "levels_changed_at": "2026-10-18T21:19:51-06:00",
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
 "daily_levels_last_days": 16,
 "days": 14,
 "expected_next_week": 8.0,
 "reserve_levels": 108,
 "reserve_pacing_ok": 107,
 "weeks_to_next_batch_from_daily_bot": 0.0
}
```

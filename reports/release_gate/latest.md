# Release gate — HOLD

_2026-10-08T15:59:52+00:00_

## HOLD
- [batches] nothing new to ship since the last deploy

## Warnings
- [live] live WebGL built 2026-10-01 21:11 but levels changed 2026-10-07 01:47 — site serves the old campaign
- [forecast] reserve has 60 pacing-ok levels; at 2.5/week from the daily bot the next batch of 100 is ~16 weeks away — use production.batch_generator for batches

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
 "moved_positions": 0,
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
 "levels_changed_at": "2026-10-07T01:47:12-06:00",
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
 "daily_levels_last_days": 5,
 "days": 14,
 "expected_next_week": 2.5,
 "reserve_levels": 93,
 "reserve_pacing_ok": 60,
 "weeks_to_next_batch_from_daily_bot": 16.0
}
```

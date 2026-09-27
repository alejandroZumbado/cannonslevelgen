# Release gate — HOLD

_2026-09-27T02:56:46+00:00_

## HOLD
- [batches] 306 new levels since the last deploy — not a multiple of 100; ship 300 or wait for 94 more

## Warnings
- [levels] 75 release levels fail the pacing gate (breathers are expected to), e.g. [43, 14, 449, 225, 27, 5, 64, 32]
- [batches] 196 of 200 already-deployed positions changed level (first: position 2) — players' saved progress would point elsewhere (allowed only because the game isn't published)
- [live] live WebGL built 2026-09-23 09:19 but levels changed 2026-09-27 02:46 — site serves the old campaign
- [forecast] reserve has 1 pacing-ok levels; at 3.5/week from the daily bot the next batch of 100 is ~28 weeks away — use production.batch_generator for batches

## levels
```json
{
 "count": 506,
 "classifications": {
  "champion_win": 472,
  "solved_by_search_only": 34
 },
 "pacing_failures": 75
}
```

## batches
```json
{
 "deployed": 200,
 "current": 506,
 "new": 306,
 "moved_positions": 196,
 "deployed_at": "2026-09-23T09:19:49-06:00",
 "snapshot_derived": true
}
```

## live
```json
{
 "url": "https://alejandrozumbado.github.io/cannons-build/",
 "files": {
  "Cannons.data.unityweb": {
   "status": 200,
   "remote_bytes": 46139031,
   "local_bytes": 46139031
  },
  "Cannons.framework.js.unityweb": {
   "status": 200,
   "remote_bytes": 74718,
   "local_bytes": 74718
  },
  "Cannons.loader.js": {
   "status": 200,
   "remote_bytes": 117893,
   "local_bytes": 117893
  },
  "Cannons.wasm.unityweb": {
   "status": 200,
   "remote_bytes": 6737398,
   "local_bytes": 6737398
  }
 },
 "index_status": 200
}
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
{
 "learning.yml": [
  "success",
  "success",
  "success"
 ],
 "daily_production.yml": [
  "success",
  "success",
  "success"
 ],
 "weekly_level_audit.yml": [
  "success",
  "success"
 ]
}
```

## forecast
```json
{
 "daily_levels_last_days": 7,
 "days": 14,
 "expected_next_week": 3.5,
 "reserve_levels": 3,
 "reserve_pacing_ok": 1,
 "weeks_to_next_batch_from_daily_bot": 28.3
}
```

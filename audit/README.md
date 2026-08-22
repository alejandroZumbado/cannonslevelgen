# audit/

Full paper trail of every LLM call this project makes, one file per day:
`audit/YYYY-MM-DD.jsonl` (one JSON object per line, one line per call). Not
checked into git (see `.gitignore`) — it can hold sensitive-ish prompt/response
text and grows daily; `report.py` and this file are the only tracked contents.

Each line has, in full: timestamp, which module made the call (`caller`:
`strategy_learner` / `level_designer` / `daily_generator`), provider + model,
exact `tokens_used` for that one call, the daily budget remaining right after
it, the complete system + user prompt, the complete raw response, and a small
`outcome` dict recording what the call actually achieved (policy promoted?
rule confirmed? level accepted? nothing?).

This is deliberately separate from two other places that look similar but
answer different questions:
- `state/budget.json` — just the running daily token counter, checked on
  every call before spending more. Cheap, aggregate, not human-facing.
- `learning_log/YYYY-MM-DD.md` — short human-readable narrative of the day.
  Good for a quick read; doesn't have exact token costs or full prompts.
- `audit/YYYY-MM-DD.jsonl` (here) — the ground truth. If the other two ever
  seem to disagree with what actually happened, this is what to check.

## Reading it

```
python audit/report.py                 # today's rollup: tokens by caller, what got achieved
python audit/report.py 2026-08-22       # a specific day
python audit/report.py --all            # totals across every day recorded so far
python audit/report.py 2026-08-22 -v    # also print truncated prompt/response per call
```

For anything report.py doesn't show, the JSONL is plain and greppable:
```
python -c "import json; [print(json.loads(l)['user_prompt']) for l in open('audit/2026-08-22.jsonl', encoding='utf-8')]"
```

# Run Planning — Workflow

Estimate the day's run: current fitness to a projected pace, finish time and
effort band for a planned distance. Feeds both the playlist (duration and arc)
and the digest (next-week plan).

## Workflow

1. **Get the planned distance.** Ask the runner if they haven't said.
2. **Refresh what the projection needs.** `python3 scripts/cache.py status`.
   Planning uses `performance` (recent best efforts — this is the important
   one) and `activities_history` (trailing load for the effort band);
   `athlete_zones` is the fallback anchor when no recent runs are cached.
   - `Strava:get_activity_performance` on the latest few runs →
     `python3 scripts/cache.py put performance --activity-id <id> --file -`
   - `Strava:list_activities` for recent weeks → `put activities_history`
   - `Strava:get_athlete_zones` → `put athlete_zones`
3. **Project it.** `python3 scripts/planning/plan_run.py <km>`.
4. **Report** the anchor effort, the race-effort projection, and the
   recommended band with its reasoning. The band matters more than the raw
   projection: Riegel projects a *race*, and most runs should not be one.
5. **Hand off** using the commands the script prints — copy them rather than
   composing your own, because they carry `--start-in` through:
   `build_playlist.py <km>` sizes the playlist to the recommended finish, and
   `run_pipeline.py --plan-km <km> --start-in <hours>` makes the digest lead
   with the planned run. Both re-project through this same model, so the
   numbers agree exactly; there is nothing to copy by hand. Piping
   `plan_run.py <km> --json` into `run_pipeline.py --plan -` does the same.

## Scripts

**`plan_run.py`** — the whole flow in one call.

```bash
python3 scripts/planning/plan_run.py 5
python3 scripts/planning/plan_run.py 8 --start-in 10   # running this evening
python3 scripts/planning/plan_run.py 10 --band easy --json
python3 scripts/planning/plan_run.py 10 --exp 1.07     # tune Riegel
```

When the runner names a time rather than "now" ("this evening", "tomorrow
morning"), pass the hours until then with `--start-in`. Rest accrues until the
run starts, and the band rule turns on hours of rest, so a morning-planned
evening run is genuinely more rested than the default reading suggests.

It prints the planned distance, the anchor effort and where it came from, the
race-effort projection, the recommended band with pace and finish, and the
trailing load. `--json` returns the same structure for handing to the playlist.

## The model

- **Anchor**: the fastest cached effort at or below the planned distance,
  preferring recent runs over personal bests, because the question is current
  fitness rather than lifetime peak.
- **Projection**: Riegel, `T2 = T1 x (D2 / D1) ^ 1.06`. The exponent is the
  standard endurance value; raise it with `--exp` if long projections read fast.
- **Band multipliers** on race pace: easy 1.20, steady 1.10, hard 1.02. That
  puts easy pace roughly 60-90 s/km above race pace, the usual prescription.
- **Band choice** from the rolling seven days ending now:
  - at or above 110% of the weekly target → **easy** (bank volume, skip intensity)
  - 72+ hours rested *and* under 80% of target → **hard** (quality is affordable)
  - otherwise → **steady**

  Rest is counted in hours, not days, so an evening run 13 hours ago does not
  round down to zero days of rest and unlock a hard session.

Each threshold is a named constant at the top of `plan_run.py`. `--band`
overrides the recommendation when the runner has already decided.

## Gotchas

- Runs are phone-GPS: no heart rate or cadence, so the projection leans on
  pace, best efforts, elevation and route repetition.
- **Planning and the digest quote different mileage on purpose.** Planning uses
  a rolling 7-day window ending now; the digest uses the Monday-to-Sunday week.
  A run that falls in one and not the other makes the totals differ by exactly
  that run. Say which window a number came from when reporting it.
- Trailing load and rest are measured against the clock, so a stale cache reads
  as more rest than the athlete actually had. `cache.py status` flags that before it
  matters.
- With nothing cached the script exits non-zero and names the MCP call to make.
  Do not guess a pace to work around it.

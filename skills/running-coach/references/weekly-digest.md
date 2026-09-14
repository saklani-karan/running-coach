# Weekly Run Digest — Workflow

Turns the Strava week into a warm, editorial HTML email (a "running almanac"):
cursive title and section headers, ledger-set stats against a rolling goal, the
cities run in, "between the lines" insights, and a next-week plan.

## Contents
- Workflow (the four steps, start to finish)
- Scripts (commands and what each prints)
- Goal model
- Design decisions, locked with Karan
- Context and gotchas

## Workflow

1. **Refresh the cache.** `python3 scripts/cache.py status`. The digest needs
   `activities_week`, `activities_history`, `performance`, `streams` and
   `weather`; `athlete_profile` and `athlete_zones` are optional but each one
   unlocks part of the digest (focus multiplier, personal-best comparison).
   For each gap, call the MCP tool `status` names and pipe the response into
   `cache.py put`:
   - `Strava:list_activities` for this Monday to today → `put activities_week`
   - `Strava:list_activities` for the 5 weeks before → `put activities_history`
   - `Strava:get_activity_performance` per run → `put performance --activity-id <id>`
   - `Strava:get_activity_streams` with
     `keys=distance,altitude,velocity_smooth,time` per run →
     `put streams --activity-id <id>`
   - `AccuWeather:widgets-search-claude` then
     `AccuWeather:widgets-historical-claude` → `put weather`
2. **Build it.** `python3 scripts/digest/run_pipeline.py`. This validates the
   cache, analyses the week, and renders the email in one go. If a run has just
   been planned, add `--plan-km <km>` so the next-week section leads with it.
3. **Read the summary it prints** — distance against goal, the percentage, and
   which insights fired. If it prints `note:` lines, they name the MCP call that
   would fill that gap; mention them to Karan rather than silently dropping the
   content.
4. **Hand over the file path.** The HTML is self-contained and ready to send.
   There is no email connector, so do not attempt to send it.

If validation fails, the error names the exact MCP call and `cache.py put`
command that fixes it. Fix and re-run; do not edit the JSON by hand.

## Scripts

**`run_pipeline.py`** — validate, analyse, render. The normal entry point.

```bash
python3 scripts/digest/run_pipeline.py
python3 scripts/digest/run_pipeline.py --plan-km 8 --start-in 11   # tonight's run
python3 scripts/planning/plan_run.py 8 --start-in 11 --json | \
    python3 scripts/digest/run_pipeline.py --plan -               # same result
python3 scripts/digest/run_pipeline.py --seed 3 --out /tmp/digest
```

Carry `--start-in` through whenever planning used it: the digest re-projects
the run, and without the same start time it would band the run as if it were
starting now and could disagree with the plan just reported.

It needs `jinja2` for the render and checks for it up front, re-execing into a
project `.venv` that has it if the current interpreter doesn't. When a step
does fail, the message says what was already written, so only the remaining
part needs redoing.

**`analyze.py`** — weekly totals, rolling goal, cities, insights →
`digest_data.json`. Prints the headline numbers and the insight keys that fired.

```bash
python3 scripts/digest/analyze.py --json        # payload to stdout as well
python3 scripts/planning/plan_run.py 8 --json | \
    python3 scripts/digest/analyze.py --plan -  # or pipe a plan straight in
```

**`build_email.py`** — maps `digest_data.json` into the Jinja2 template and
writes `weekly_run_digest.html`. Prints the subject line. `--seed` fixes the
masthead title so output is reproducible.

**`insights.py`** — not a CLI. Each generator is a pure function that returns
nothing when its pattern is absent. Add an insight by adding a generator and
listing it in `GENERATORS`.

## Goal model

Strava exposes no weekly-goal field, so the target is derived from the
athlete's own mileage and moves every week:

- `baseline` = mean weekly distance over the last `TRAILING_WEEKS` complete weeks
- `goal_dist` = `baseline` x the multiplier for `current_focus`
  (build 1.10, peak 1.15, maintain 1.00, recover 0.80; build when the profile
  is not cached)
- `goal_runs` = median runs/week over the same window

The knobs are `TRAILING_WEEKS` and `FOCUS_MULTIPLIERS` at the top of
`analyze.py`, each documented with why its value is what it is. Do not
substitute a fixed target.

## Design decisions, locked with Karan

- **Cursive** (`fonts.script`, Pinyon Script with a Snell Roundhand fallback)
  for the masthead title *and* the section headers only.
- **Figures** (`fonts.figure`, Space Mono with an American Typewriter fallback)
  for ledger numbers, goal fractions, trailing weeks and city stats.
- Playfair Display sub-headings, EB Garamond body text.
- Warm paper palette, hairline rules, drop cap, curly quotes. No emoji, no
  gradients, no card grid.
- **Cities run in (the "Territory" section) replaced the route map**, built from
  each activity's `location_summary`. A route map would mean decoding polylines
  and rendering a PNG, which is the only thing that would pull matplotlib in.
- Required blocks: funky title, funky subtext, stats and goal, cities,
  intriguing stats, next-week plan.
- Fonts are Apple-native first so the email renders bespoke on Karan's Mac and
  survives clients that strip web fonts; Google Fonts are an enhancement and
  web-safe faces are the floor.
- Re-skinning means editing `THEME` / `FONTS` in `build_email.py` or the
  template. Re-contenting means changing the data. Neither needs HTML edits.

## Context and gotchas

- Runs are phone-GPS: no heart-rate or cadence streams, so insights lean on
  pace, best efforts, elevation and route repetition.
- Font CDNs are blocked in the sandbox, so the cursive cannot be previewed
  there. It renders on Karan's machine.
- `weather` returns climatology, not observed conditions. The athlete's own run
  notes are the ground truth for what the weather actually did; the climate
  normals only supply the temperature envelope.
- A missing `per_run_conditions` entry is a warning, not a failure — those runs
  simply render without a conditions line.
- The digest counts the Monday-to-Sunday week. Planning counts a rolling 7-day
  window, so the two legitimately quote different mileage; say which one a
  number came from.
- **A digest built before Sunday midnight reports a partial week.** The
  pipeline says so (`the week is still open`), the email says "with today still
  to run" instead of "fell short", and a run passed via `--plan-km` is credited
  to *this* week with the projected new total, not to next week. Repeat that
  caveat when handing the digest over — the percentage is not final.

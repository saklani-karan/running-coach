# Week Planning — Workflow

Turn the athlete's weekly goal into a laid-out training week and, finally, into
Google Calendar blocks — one event per run, each with pace, structure, the
reason it's on the plan, and a little motivation. Where `plan_run.py` projects a
single run, `plan_week.py` plans the whole week.

**The scripts compute; you write.** Every number in a calendar block comes from
`plan_week.py`, and every word comes from you. There are no canned "why this
run" paragraphs or stock jokes in the codebase, because a stock joke is the same
joke every Tuesday forever. The `brief` stage hands you each run's facts and the
variable contract; you write the prose; `events --content` renders it.

## Workflow

1. **Find the goal.** Check Strava first — `Strava:get_training_plan` and
   `Strava:get_athlete_profile` (`current_focus`). Strava has **no writable
   weekly-goal field**, so if there's no explicit target, ask the runner (a
   weekly distance and how many runs, or a race and date), then persist it —
   the skill can't write it back to Strava:
   ```bash
   python3 scripts/cache.py put goal_state --set weekly_km=28 \
       --set runs_per_week=5 --set focus=build
   # or a race goal:
   python3 scripts/cache.py put goal_state --set type=race \
       --set race_name="TCS 10K" --set race_date=2026-11-16 \
       --set weekly_km=32 --set runs_per_week=5 --set focus=build
   ```
   Unknown field names are rejected rather than silently stored, so a typo
   surfaces at the write instead of as a plan that never changed.
   `python3 scripts/planning/plan_week.py goal` shows the target in force and
   whether it's stored or derived. With no goal set it derives one from trailing
   mileage (the digest model) so the flow still runs — but confirm it.
2. **Refresh what the paces need.** `python3 scripts/cache.py status`. Week
   planning reads two different things from the cache, because easy running and
   hard running are evidenced differently:
   - `activities_history` / `activities_week` — each run's average speed and
     distance, over the last three weeks, giving the **average** and **quickest**
     pace that easy, steady, long and tempo are built from. `activities_history`
     also derives the goal when none is stored.
   - `performance` and `athlete_zones` — cached **best efforts**, which price
     interval pace and cap tempo. Without them tempo falls back to recent form
     and rep pace is only an estimate; `propose` says which anchor it used.

   Refresh via `Strava:list_activities` and `Strava:get_activity_performance`.
   Optionally cache `weather_forecast` — `AccuWeather:widgets-search-claude` for
   the city then `AccuWeather:widgets-daily-claude` for its location key — so the
   brief carries each run day's conditions for you to write the weather line
   from; without it there's simply nothing to write it from.
3. **Propose the split.** `python3 scripts/planning/plan_week.py propose`. It
   prints the recent-form line and the date range behind it, a **pace-windows-by-
   distance table** (easy / steady / tempo / reps at 5 / 10 / 15 km) and the
   **split table** — a run mix (long + a quality session for a build/peak focus +
   easy/steady), every distance a multiple of 5 km, each row showing the pace
   window, estimated time and focus. Any gap between the goal and the plan is
   printed as a **Note** (see *Reconciliation*). **Confirm the count and the mix
   with the runner**; `--runs N` / `--km D` override.
4. **Draft the calendar.** `python3 scripts/planning/plan_week.py draft
   --start-date <any date in the week> --time 07:00 [--weekend-time 18:00]
   --location "<start point>"`. Long run to Sunday, quality days spaced Tue/Thu,
   easy days between, the rest are rest days; `--weekend-time` gives Sat/Sun a
   different clock. Planning mid-week, a run whose slot has already passed is
   moved to a free later day so the weekly total holds (see *Filling the week*).
   **Confirm days and times** with the runner.
5. **Take the brief and write the content.** `python3
   scripts/planning/plan_week.py brief`. For each scheduled run it prints the
   date, time, distance, pace window, estimated time, session shape and that
   day's forecast, then the contract your prose has to satisfy. `--json` gives
   you the same facts plus the contract as JSON. The contract on its own:
   ```bash
   python3 scripts/planning/calendar_event.py --schema        # human
   python3 scripts/planning/calendar_event.py --schema --json # machine
   ```
   Write one JSON object per run, keyed by the run's date. Quote the plan's
   numbers verbatim — never recompute a pace — and make each run's words its own.
   Seven blocks that read identically are worse than none: the athlete stops
   reading them, and then stops reading the one that mattered.
6. **Render and create the events.** `python3 scripts/planning/plan_week.py
   events --content week-content.json --json`. Each event carries `summary`, the
   rendered HTML `description`, `location`, `startTime`, `endTime`, `timeZone`
   and a 30-min reminder. Feed each to `Google_Calendar:create_event`, one call
   per event. Nothing is created until you make those calls — read them back to
   the runner first.

Each stage takes `--json`, and `draft` / `brief` / `events` accept the previous
stage's `--json` on stdin (`--split -` / `--draft -`), so a split the runner
edited carries through unchanged:
```bash
python3 scripts/planning/plan_week.py propose --json \
 | python3 scripts/planning/plan_week.py draft --split - --start-date 2026-09-14 --json \
 > draft.json
python3 scripts/planning/plan_week.py brief --draft draft.json --json   # write against this
python3 scripts/planning/plan_week.py events --draft draft.json \
    --content week-content.json --tz Asia/Kolkata --json
```

## The description contract

`assets/calendar_event.tmpl` is the layout; `scripts/planning/calendar_event.py`
is the contract and the renderer. Its only jobs are to declare each variable's
name, type and purpose, to validate what you supply, and to substitute it. It
derives nothing.

| Variable | Type | What to write |
| --- | --- | --- |
| `title` | string | The headline — session and distance |
| `metrics` | string | Pace window, estimated time, effort band, from the plan |
| `structure` | string | How to run it, in one line |
| `progress` | string (optional) | Where it sits in the week |
| `why` | string | Why this run earns its place in *this* week |
| `prep` | string[] | Conditions, warm-up, fuel, kit — one line each |
| `checklist` | string[] | Tick items, in the order they happen |
| `pep` | string | One sentence of real encouragement |
| `quip` | string | One dry, funny line |
| `footer` | string | Which plan this block belongs to |

`--schema` prints the same table with the full reasoning and an example for each.
Validation reports every problem at once and names the run it belongs to, so one
round trip is enough to fix a whole week's content.

Rendering a single description without a plan around it:
```bash
python3 scripts/planning/calendar_event.py --content one-run.json          # raw HTML
python3 scripts/planning/calendar_event.py --content one-run.json --text   # to read
python3 scripts/planning/calendar_event.py --content week.json --html > preview.html
python3 scripts/planning/calendar_event.py --placeholders --text           # layout only
```
Edit the asset to restyle without touching Python. Its newlines collapse to
single spaces, so only literal `<br>` tags break lines; write `$$` for a literal
dollar sign. A placeholder the contract doesn't define, or a stray `$`, fails
with a message naming the available variables rather than a traceback.

## Filling the week (mid-week planning)

`draft` / `brief` / `events` default to **keeping the weekly total**. Plan
partway through the week and any run whose start time has already passed is not
dropped — it is moved to the next free day still ahead, so a 25 km / 4-run week
stays 25 km / 4 runs even when planned on a Monday afternoon.

When there are **more runs than days left**, the total cannot hold. Rather than
park the overflow on days already gone and report a week the calendar never
receives, the lowest-priority runs are dropped and named:

```
Week: 2026-09-14 to 2026-09-20 · 20 km / 3 runs · 06:30 · HSR Layout, Bengaluru
      30 km was proposed; 10 km of it falls on days already gone and will not be created.
  Dropped: Easy run 5 km, Easy run 5 km — no day left in the week for them
```

With no usable day left at all, `draft` errors and suggests next week's Monday.
The weekly-progress figures count only the runs that will actually be created,
so a block never says "run 2 of 5" in a week the calendar sees three of. Hard
days are re-checked for adjacency **after** any reflow, and a pair that couldn't
be separated is reported rather than left for the legs to discover.

Pass `--no-fill-week` for the old behaviour (drop the past-slot runs; `events`
then skips them unless `--include-past`). "Now" is the machine clock,
overridable with `--as-of <ISO datetime>` for testing.

## Reconciliation

Three rules quietly move the numbers: distances round to 5 km blocks, no single
run may exceed what the athlete has actually run, and every run gets at least one
block. Left unsaid they look like arithmetic errors, so `propose` prints each one
that bit:

```
Goal: 20 km / 5 runs (derived, focus build)
Proposed split — 25 km, 5 runs (every distance a multiple of 5 km)
  Note: 5 runs cannot total less than 25 km when the shortest run is 5 km — the
        plan is 25 km. Drop to 4 runs to hit 20 km exactly.
  Note: the long run is only 5 km, no further than another run this week — at
        this volume it is the week's anchor in name only.
```

The same block appears under `reconciliation` in `propose --json`. Read the notes
to the runner; they are the difference between a plan and a mystery.

## The model

- **Two pace anchors, because two different things are being measured.**
  - Easy, steady and long come off the **average of recent whole runs** — what
    this athlete does on a normal day. Easy sits `EASY_OVER_AVG` (8 s/km) slower
    than that average, steady is the average, and every band drifts slower by
    distance (`DIST_DRIFT_PER_KM`, 4 s/km beyond 5 km).
  - Tempo starts from the **quickest full run** in the window, `TEMPO_UNDER_BEST_RUN`
    (10 s/km) faster, and is capped by a Riegel race projection (`TEMPO_OVER_RACE`):
    a Tuesday should never out-run a projected threshold.
  - Reps come **only** from a race projection at 400 m (`REP_OVER_RACE`), because
    a whole run's average says nothing about 400 m pace.

  Projections use `best_projection`, which takes the **fastest** projection across
  every cached effort rather than the nearest distance below the target. Strava's
  per-activity "best efforts" are only the quickest stretch *within* that
  activity, so an easy run contributes an easy 400 m; letting a strong 5 km speak
  for the 400 m it implies is the whole point of having several data points.
  Effort evidence that projects **slower** than the athlete's own quickest run is
  stale — an old PB, or a jogged split — and is discarded rather than planned
  around. `propose` names the anchor it used either way.
- **Band ordering is guaranteed.** Two anchors drawn from different data can
  cross, so `band_centers` walks from the slowest band upward and pulls any
  colliding band *faster*, never slowing the easy end. That direction is
  deliberate: recent averages are the strongest evidence in the cache, and
  slowing easy pace to accommodate an odd projection would corrupt the runs the
  athlete does most of.
- **Pace is read over the last 21 days** (`PACE_LOOKBACK_DAYS`), widening to 56
  if that's too thin to average, because pace read across a year of running
  describes a year-old athlete. `propose` prints the date range it used.
- **Session times are priced piece by piece.** A tempo or interval session spends
  most of its distance at easy pace, so `session_duration` charges the warm-up,
  cool-down and jog recoveries at easy pace and only the working part at the
  session's headline pace. Pricing an interval day at rep pace for all 5 km
  books a calendar block that ends while the runner is still out.
- **Distances are always multiples of 5 km** (`DISTANCE_STEP_KM`) — milestones
  matter. Every run starts at one 5 km block, the **long run takes the second
  block before anything else gets one** (otherwise at low volume the "long run"
  ends up the same length as Monday's recovery jog), then extra blocks go to the
  runs that should carry the most volume first. Caps come from the athlete's own
  `longest_km` (`CAP_OF_LONGEST`: 1.5× for the long run, 1× for everything else,
  with floors), so nobody meets their longest-ever distance on a Tuesday.
- **The mix** depends on the run count and focus: a build/peak focus earns a
  tempo session from three runs up and intervals from six; a maintain/recover
  focus stays aerobic. See `split_template()`.
- **Day assignment** anchors the long run on Sunday and the quality days on
  Tue/Thu; easy days fill Mon/Wed/Fri and Saturday, and unused days are rest.
  Hard days (long, tempo, intervals) avoid landing next to each other, and the
  guarantee is re-checked after any mid-week shuffle rather than assumed from the
  template.
- **Run counts** come from the median of recent complete weeks. The week in
  progress counts only to **hold the ramp back**, never to justify raising it,
  and a last complete week that fell short of the median caps the plan — with a
  note, so a step up is never mistaken for a current habit.

## Google Calendar

`events` emits payloads, it does not call the connector — the agent does, the
same split that keeps the rest of the skill tokenless. Map each event's fields
onto `Google_Calendar:create_event` (`summary`, `description`, `location`,
`startTime`, `endTime`, `timeZone`, `overrideReminders`). Default timezone is
`Asia/Kolkata` and default start time 06:30 — override with `--tz` / `--time`
for a different athlete.

The description is HTML, and Calendar renders only a **limited subset** —
`<b>`, `<i>`, `<u>`, `<br>`, `<ul>`, `<li>`, `<a>`. It shows `<small>` at normal
size and collapses `&nbsp;`, so the template uses neither. Keep any edit inside
that subset. `calendar_event.py --html` writes a browser preview, but the app is
the only real test.

## Flags

Every flag of `plan_week.py`. Run `<stage> --help` for the same list at the CLI.

**Shared by all five stages**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--data DIR` | `~/.running-coach/data` | cache directory to read |
| `--km KM` | goal's distance | override the weekly distance (rounded to ×5) for this run only, without editing `goal_state` |
| `--runs N` | goal's run count | override the number of runs (1–7) for this run only |
| `--json` | off | emit the stage's JSON instead of the human table (this is what feeds the next stage / the connector) |

`goal` and `propose` take only the shared flags.

**Scheduling — `draft`, `brief` and `events`**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--split -` \| `--split FILE` | recompute | read a `propose --json` split from stdin (`-`) or a file, so a split the runner edited carries through |
| `--start-date YYYY-MM-DD` | today | any date in the target week (the Monday–Sunday week containing it) |
| `--time HH:MM` | `06:30` | weekday start time |
| `--weekend-time HH:MM` | same as `--time` | Sat/Sun start time (e.g. an evening long run) |
| `--location "…"` | `HSR Layout, Bengaluru` | starting point, written to each event's location |
| `--no-fill-week` | off (fill on) | drop runs whose slot is already past instead of moving them to a free later day |

**`brief` — also**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--draft -` \| `--draft FILE` | recompute | read a `draft --json` from stdin (`-`) or a file; when given, the scheduling flags are ignored |
| `--include-past` | off | also brief days already past in the week |

**`events` — also**

| Flag | Default | Meaning |
| --- | --- | --- |
| `--draft -` \| `--draft FILE` | recompute | as for `brief` |
| `--content FILE` \| `--content -` | **required** | the descriptions you wrote, one object per run keyed by date |
| `--tz IANA` | `Asia/Kolkata` | timezone stamped on every event's `timeZone` |
| `--include-past` | off | also emit events for days already past in the week |

**`calendar_event.py`**

| Flag | Meaning |
| --- | --- |
| `--schema` | the variables to write, with types and reasons (add `--json` for machine-readable) |
| `--content FILE\|-` | render written content: one object, a list, or an object keyed by date |
| `--placeholders` | render with bracketed markers, to work on the template's layout |
| `--summary "…"` | event title, shown only in the `--html` / `--text` preview chrome |
| `--text` / `--html` / `--json` | to read in a terminal / a browser preview / the rendered HTML as JSON |

**Testing:** `--as-of <ISO datetime>` (hidden) overrides "now" for the
fill-week logic, so a plan is reproducible regardless of the wall clock.

## Gotchas

- **Strava can't store the goal.** The MCP is read-only for goals, so the
  target lives in `goal_state` in the cache, not on Strava. Say so when the
  runner expects it to appear in the Strava app.
- **The clock matters.** `draft --start-date` defaults to today. For a fresh
  Monday-to-Sunday plan, pass the Monday.
- **`events` will not invent a description.** Without `--content` it refuses to
  run, and content missing a scheduled date fails naming that date. This is
  deliberate: a calendar block with a generated-looking description is worse
  than no calendar block.
- **Three weekly figures are not three bugs.** `plan_run` counts a rolling
  7-day window, the digest counts the Monday-to-Sunday week, and `plan_week`
  plans a 5 km-rounded target. When quoting more than one to the runner, say
  which is which.
- With nothing cached to project from, `propose` exits non-zero and names the
  MCP call to make. Don't invent a pace to get past it.

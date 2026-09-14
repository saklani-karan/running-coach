---
name: running-coach
description: >-
  Plans a run or a whole week as Google Calendar blocks, builds a weekly
  running digest email, and makes playlists — from Strava, Spotify, AccuWeather
  and Calendar. Use for pacing or planning a week.
---

# Running Coach

Four capabilities over one athlete's data, sharing one data cache and the pace
maths in `common/pace.py`. Work out which one is wanted, then read only that
capability's reference.

| Capability | Reference | Entry point |
| --- | --- | --- |
| Plan a run | [references/run-planning.md](references/run-planning.md) | `scripts/planning/plan_run.py` |
| Plan the week | [references/week-planning.md](references/week-planning.md) | `scripts/planning/plan_week.py` |
| Weekly digest | [references/weekly-digest.md](references/weekly-digest.md) | `scripts/digest/run_pipeline.py` |
| Run playlist | [references/run-playlist.md](references/run-playlist.md) | `scripts/playlist/build_playlist.py` |

Asked for more than one ("plan a 10k and make the playlist")? Plan first. It
prints the exact follow-on commands, which carry the distance and start time
into the playlist and the digest — use those rather than composing your own.

**Run the scripts, don't read them.** They print everything needed; every one
takes `--help` and most take `--json`; the shared `--data DIR` points any
script at a different cache. Reading their source wastes context.

## Refresh the cache first

The scripts never call MCP and never use an API token. Fetching is this agent's
job; `scripts/cache.py` is the only thing that writes the cache.

```bash
python3 scripts/cache.py status          # what's cached, how stale, what to call
```

For every key it reports as missing or stale, call the MCP tool it names, then
pipe that tool's response in verbatim:

```bash
python3 scripts/cache.py put activities_week --file -
python3 scripts/cache.py put performance --activity-id ACTIVITY_ID --file -
python3 scripts/cache.py show performance          # compact summary (--raw for the JSON)
python3 scripts/cache.py validate --for digest     # confirm before building
```

Flags: `put` takes `--file -`/`--file PATH` (or `--set key=value` for simple
local state), `--activity-id ID` for per-activity keys, and `--replace` to
overwrite a per-activity key instead of merging into it; `show` takes `--json`
(summary as JSON) and `--raw` (dump the cached JSON itself); `validate` takes
`--for {digest,planning,playlist}`. All commands accept `--data DIR`.

`status` is cheap and always worth running first. A stale key still loads, so
refresh only what the answer actually depends on. Never hand-edit the JSON.

## Data sources

- **Strava** — `Strava:list_activities`, `Strava:get_activity_performance`,
  `Strava:get_activity_streams`, `Strava:get_athlete_profile`,
  `Strava:get_athlete_zones`.
- **Spotify** — `Spotify:search`, `Spotify:generate_playlist`,
  `Spotify:save_to_library`, `Spotify:remove_from_library`. These can generate,
  save and remove a playlist but cannot rename one or edit its tracks, so every
  run produces a new playlist.
- **AccuWeather** — `AccuWeather:widgets-search-claude` to resolve a city to a
  location key, then `AccuWeather:widgets-historical-claude` for run dates. It
  returns climatology rather than observed conditions, so the athlete's own run
  notes are the ground truth for what the weather actually did.
- **Google Calendar** — `Google_Calendar:create_event`, one call per planned run,
  to write the week's blocks. Only the week-planning capability uses it, and only
  after the runner has confirmed the schedule; the script emits the payloads,
  the agent makes the calls.

The `Server:tool` form above names the server and the tool, not the callable
name, which varies by client. This skill bundles no connectors: it uses
whatever Strava, Spotify and AccuWeather connectors the session already has.
Match on the server and tool names and use whichever form is offered. If none
is connected, say so rather than guessing at numbers.

## Layout

```
scripts/
  cache.py      the only gateway to the cache: status · put · show · validate
  common/       pace.py (incl. Riegel) · weeks.py · normalize.py · paths.py
  planning/     plan_run.py · plan_week.py (week → calendar)
  digest/       run_pipeline.py · analyze.py · insights.py · build_email.py
  playlist/     build_playlist.py
references/     run-planning.md · week-planning.md · run-playlist.md · weekly-digest.md
assets/         digest_email.html · calendar_event.tmpl
```

Python 3.10+ and nothing else — no third-party packages, so `python3` works
as-is with no virtualenv and no install step. The cache and the rendered
digest are written outside the skill, under `~/.running-coach/` unless
`$RUNNING_COACH_DATA` / `$RUNNING_COACH_OUTPUT` say otherwise.

A planning or digest figure that looks inconsistent usually is not, because the
three capabilities legitimately measure different things: `plan_run` reports a
rolling 7-day window ending now, the digest reports the Monday-to-Sunday week,
and `plan_week` plans a target rounded to 5 km. Each labels which it means, and
`plan_week propose` prints a note for every gap between the goal and the plan.
Quote more than one figure only with its window named.

## Conventions

- Pace formatting, the Riegel projection and `best_projection` live in
  `common/pace.py`. Reuse them; do not re-derive the maths inline.
- The two planners price paces differently on purpose, because they answer
  different questions. `plan_run` projects one run as a race effort and steps
  down by band. `plan_week` prices easy running off the athlete's recent
  averages and only quality work off effort projections, since a week of easy
  runs prescribed at projected pace is a week nobody runs. Don't "unify" them
  without reading both references.
- The digest goal is derived from the athlete's own trailing mileage, never
  hardcoded. Keep it that way — see the goal model in the digest reference.
- The weekly goal is the athlete's, and Strava can't store it: the MCP has no
  writable goal field, so a stated goal is persisted locally under `goal_state`
  in the cache. Derive one from trailing mileage only as a fallback, and confirm
  it before planning the week.
- Insights must come from the data. `digest/insights.py` generators return
  nothing when their pattern is absent, so a quiet week yields fewer insights
  rather than invented ones.
- Mutable state (the pinned playlist) belongs in the cache under
  `playlist_state`, not in these markdown files.
- The digest output is a self-contained, ready-to-send HTML file. No email
  connector is wired up, so hand over the file path.
- The week plan's calendar description is rendered from `assets/calendar_event.tmpl`
  (a `string.Template`, like the digest's HTML asset): edit that file to change the
  layout or section headers without touching Python.
- **Calendar descriptions are written by the agent, not generated by the
  scripts.** `planning/calendar_event.py` declares each template variable's type
  and purpose and renders what it's given; there are no canned reasons, pep talks
  or jokes anywhere in the tree. Run `plan_week.py brief` for the facts and the
  contract, write the prose, then `events --content`. If you find yourself adding
  a phrase to a Python file, that's the mistake this split exists to prevent.

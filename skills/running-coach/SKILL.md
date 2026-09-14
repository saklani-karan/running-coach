---
name: running-coach
description: >-
  Plans runs, builds a weekly running digest email, and generates run playlists
  for Karan from his Strava, Spotify and AccuWeather data. Use when he wants to
  plan a run or estimate pace and finish time for a distance ("plan my run",
  "how fast should I run 10k", "how long will 8 km take"); when he wants his
  weekly run recap ("weekly run digest", "run recap email", "my running week");
  or when he wants a playlist sized to a run ("make my run playlist", "today's
  running playlist"). Also use for any question about his Strava runs, run pace,
  weekly mileage versus goal, or running music, even when the skill is not named.
---

# Running Coach

Three capabilities over one athlete's data, sharing one pace model and one data
cache. Work out which one is wanted, then read only that capability's reference.

| Capability | Reference | Entry point |
| --- | --- | --- |
| Plan a run | [references/run-planning.md](references/run-planning.md) | `scripts/planning/plan_run.py` |
| Weekly digest | [references/weekly-digest.md](references/weekly-digest.md) | `scripts/digest/run_pipeline.py` |
| Run playlist | [references/run-playlist.md](references/run-playlist.md) | `scripts/playlist/build_playlist.py` |

Asked for more than one ("plan a 10k and make the playlist")? Plan first. It
prints the exact follow-on commands, which carry the distance and start time
into the playlist and the digest — use those rather than composing your own.

**Run the scripts, don't read them.** They print everything needed; every one
takes `--help` and most take `--json`. Reading their source wastes context.

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
python3 scripts/cache.py validate --for digest    # confirm before building
```

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

The `Server:tool` form above names the server and the tool, not the callable
name, which differs by how this is installed. The plugin bundles its own
connectors, so its tools arrive scoped as
`mcp__plugin_running-coach_Strava__list_activities`; a standalone install uses
whatever Strava, Spotify and AccuWeather connectors the session already has.
Match on the server and tool names and use whichever form is offered.

## Layout

```
scripts/
  cache.py      the only gateway to the cache: status · put · show · validate
  common/       pace.py (incl. Riegel) · weeks.py · normalize.py · paths.py
  planning/     plan_run.py
  digest/       run_pipeline.py · analyze.py · insights.py · build_email.py
  playlist/     build_playlist.py
references/     run-planning.md · run-playlist.md · weekly-digest.md
assets/         digest_email.html.j2
```

Everything is standard library except the digest email, which needs `jinja2`.
The digest scripts find a project `.venv` that has it and re-exec into that
interpreter automatically, so `python3 scripts/digest/run_pipeline.py` is the
right command either way. If no interpreter has it, the error says so.

A planning or digest figure that looks inconsistent usually is not: planning
reports a rolling 7-day window ending now, the digest reports the
Monday-to-Sunday week. Both label which they mean.

## Conventions

- Pace formatting and the Riegel projection live in `common/pace.py`. Reuse
  them; do not re-derive the maths inline.
- The digest goal is derived from the athlete's own trailing mileage, never
  hardcoded. Keep it that way — see the goal model in the digest reference.
- Insights must come from the data. `digest/insights.py` generators return
  nothing when their pattern is absent, so a quiet week yields fewer insights
  rather than invented ones.
- Mutable state (the pinned playlist) belongs in the cache under
  `playlist_state`, not in these markdown files.
- The digest output is a self-contained, ready-to-send HTML file. No email
  connector is wired up, so hand Karan the file path.

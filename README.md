# running-coach

A portable **Agent Skill** that turns Strava, Spotify and AccuWeather data into
three things:

1. **Plan runs** — recent Strava efforts to an estimated pace, a projected
   finish (Riegel), and an effort band from the trailing load.
   `scripts/planning/plan_run.py`.
2. **Weekly digest** — the Strava week as a self-contained HTML email: stats
   against a rolling goal derived from the athlete's own mileage, the cities
   run in, data-driven insights, and a next-week plan.
   `scripts/digest/run_pipeline.py`.
3. **Run playlist** — a playlist sized to the projected finish time, matched to
   taste and recent listening. `scripts/playlist/build_playlist.py`.

## How it's wired

The agent is the only thing that talks to MCP. `scripts/cache.py` is the only
thing that writes the cache. Everything else is a deterministic, offline
transform over that cache — no HTTP client, no API keys, no access tokens.

```
agent --(Strava: / Spotify: / AccuWeather: MCP)--> cache.py put --> the cache
the cache --> plan_run.py / run_pipeline.py / build_playlist.py --> output
```

That split is what makes the skill portable: it never needs credentials of its
own, and it runs the same whether the connectors are local, remote or absent.

## Layout

`skills/running-coach/` is the skill and is entirely self-contained — copy that
one directory anywhere and it works. Everything at the repo root is scaffolding
that stays out of the published artifact. The repo is pure source: no cache, no
generated output, nothing to clean.

```
running-coach/
├── skills/running-coach/          THE SKILL — portable, spec-compliant
│   ├── SKILL.md                   entry point; routes to the 3 capabilities
│   ├── references/                workflow specs, read on demand
│   ├── scripts/                   cache.py + common/ + the 3 capabilities
│   └── assets/                    digest_email.html
├── evals/                         three evaluation scenarios
└── package.py                     validates and zips the skill, into dist/
```

## Installing

The skill follows the Agent Skills spec — `SKILL.md` with `name`/`description`
frontmatter, the directory named to match — so anything that scans a skills
root picks it up. Symlink or copy `skills/running-coach` into that root:

| Agent | Root |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| OpenClaw | `~/.agents/skills/`, `~/.openclaw/skills/`, or `<workspace>/skills/` |
| OpenCode | `~/.config/opencode/skills/`, or the `.claude` / `.agents` roots |

```bash
ln -s "$PWD/skills/running-coach" ~/.claude/skills/running-coach
```

For claude.ai, build the archive and upload it under Settings → Capabilities →
Skills:

```bash
python3 package.py
```

That writes `dist/running-coach.zip`, holding a single `running-coach/`
directory at its root — an archive of loose files is rejected. Clients that
expect the `.skill` extension take the same archive renamed.

Two things to know about the hosted sandbox on claude.ai. There are no bundled
connectors, so the session needs its own Strava, Spotify and AccuWeather
connectors for the agent to refresh the cache. And its filesystem is
per-session, so the cache starts empty each time and is rebuilt from MCP rather
than reused — everything still works, it just re-fetches. Locally the cache
persists and `cache.py status` only asks for what has gone stale.

## Running it

Everything reads the cache and writes generated files beside it. From
`skills/running-coach/`:

```bash
python3 scripts/cache.py status                    # what's cached, how stale
python3 scripts/planning/plan_run.py 8             # plan an 8 km run
python3 scripts/digest/run_pipeline.py --plan-km 8 # digest, leading with that run
python3 scripts/playlist/build_playlist.py 8       # build the Spotify prompt
```

Every script takes `--help`, and most take `--json`. `cache.py status` names
the exact MCP call behind each gap, which is how the agent knows what to fetch.

## Where the data goes

The cache and generated output live outside the skill, so the installed copy
stays read-only and a clone resolves the same as any other install.
`cache.py status` prints the directory it resolved, the first of these that
applies:

| Condition | Cache and output |
| --- | --- |
| `RUNNING_COACH_DATA` / `RUNNING_COACH_OUTPUT` set | those paths |
| Otherwise | `~/.running-coach/{data,output}` |

Never commit a copy of the cache: it holds real athlete data, including GPS
polylines that decode to routes from home.

## Requirements

Python 3.10+ and nothing else. No third-party packages, no virtualenv, no
install step — `python3` works as-is, including inside a hosted sandbox where
installing packages is not possible.

## Packaging

`python3 package.py --validate` checks the frontmatter against the Agent Skills
spec: the six allowed keys, a kebab-case `name` matching the directory, and a
`description` within limits. It also warns past 200 characters, which is
roughly where claude.ai's picker truncates. `python3 package.py` runs the same
checks and then writes the archive.

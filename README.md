# running-coach

Karan's personal running assistant. The deliverable is a portable **Agent
Skill** at `skills/running-coach/`, with a Claude **plugin** wrapped around the
repo root that adds the Strava / Spotify / AccuWeather connectors. Three
capabilities:

1. **Plan runs** — recent Strava efforts to an estimated pace, a projected
   finish (Riegel), and an effort band from the trailing load.
   `scripts/planning/plan_run.py`.
2. **Weekly digest** — the Strava week to a self-contained HTML email: stats
   against a rolling goal derived from Karan's own mileage, the cities run in,
   data-driven insights, and a next-week plan. `scripts/digest/run_pipeline.py`.
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

## Layout

`skills/running-coach/` is self-contained and has no knowledge of the plugin
around it — copy that one directory anywhere and it works. Everything at the
repo root outside it is the Claude wrapper. The repo is pure source: no cache,
no generated output, nothing to clean.

```
running-coach/
├── skills/running-coach/          THE SKILL — portable, spec-compliant
│   ├── SKILL.md                   entry point; routes to the 3 capabilities
│   ├── requirements.txt           jinja2, and nothing else
│   ├── references/                workflow specs, read on demand
│   ├── scripts/                   cache.py + common/ + the 3 capabilities
│   └── assets/                    digest_email.html.j2
├── .claude-plugin/
│   ├── plugin.json                the Claude plugin manifest
│   └── marketplace.json           makes the repo its own marketplace
├── .mcp.json                      the 3 bundled connectors
├── hooks/hooks.json               SessionStart: builds the jinja2 venv
├── evals/                         three evaluation scenarios
└── package.py                     builds the two zips, into dist/
```

The cache and generated digests live in `~/.running-coach/`, never in the repo
— see the table at the end for the full resolution order. `evals/`, `dist/` and
`package.py` are excluded from both archives.

## Installing as a skill (any agent)

The skill follows the Agent Skills spec — `SKILL.md` with `name`/`description`
frontmatter, the directory named to match. Anything that scans a skills root
picks it up. Symlink or copy `skills/running-coach` into the relevant root:

| Agent | Root |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| OpenClaw | `~/.agents/skills/`, `~/.openclaw/skills/`, or `<workspace>/skills/` |
| OpenCode | `~/.config/opencode/skills/`, or the `.claude` / `.agents` roots |

```bash
ln -s "$PWD/skills/running-coach" ~/.agents/skills/running-coach
```

For claude.ai, build the zip and upload it under Customize → Skills:

```bash
python3 package.py --skill
```

Note that claude.ai caps skill descriptions at 200 characters and this one is
619, so that upload needs a shortened `description` first. Every other target
allows the full 1024.

Installed as a bare skill there are no bundled connectors, so the session needs
its own Strava, Spotify and AccuWeather connectors.

## Installing as a Claude plugin

This adds the three connectors and the dependency bootstrap on top of the same
skill. From GitHub, with no packaging step — in Cowork, Customize → Plugins →
Add marketplace and enter the repo URL; in Claude Code:

```bash
claude plugin marketplace add saklani-karan/running-coach
claude plugin install running-coach@karan-run-plugins
```

Or build a package for Cowork's file upload with `python3 package.py
--plugin`.

For day-to-day development, symlink the skill (as above) rather than the repo
root. Claude Code would happily load the root in place as a plugin, but then
the `SessionStart` hook writes `.plugin-data-dir` into the repo, which moves
the cache to the plugin's data directory. To exercise the wrapper — hooks,
connectors, the venv bootstrap — install it properly instead:

```bash
claude plugin marketplace add "$PWD" && claude plugin install running-coach@karan-run-plugins
```

Installing prompts for sign-in to Strava, and to Spotify and AccuWeather if you
use those capabilities. Strava's connector is its official one and needs a
Strava subscription; the other two are community servers, and their URLs are
`userConfig` fields you can repoint without editing files. Bundling also scopes
the tool names to `mcp__plugin_running-coach_Strava__list_activities`.

## Running it (dev)

Everything reads the cache and writes generated files beside it. From
`skills/running-coach/`:

```bash
python3 scripts/cache.py status                    # what's cached, how stale
python3 scripts/planning/plan_run.py 8             # plan an 8 km run
python3 scripts/digest/run_pipeline.py --plan-km 8 # digest, leading with that run
python3 scripts/playlist/build_playlist.py 8       # build the Spotify prompt
```

Every script takes `--help`, and most take `--json`.

## Where the data goes

`cache.py status` prints the directory it resolved, which is the first of these
that applies:

| Condition | Cache and output |
| --- | --- |
| `RUNNING_COACH_DATA` / `RUNNING_COACH_OUTPUT` set | those paths |
| Running as an installed plugin | the plugin's persistent data directory |
| Otherwise, including from this repo | `~/.running-coach/{data,output}` |

A plugin's install directory is replaced on update, so its cache has to live
outside it. `$CLAUDE_PLUGIN_DATA` holds that path but is only promised to hook
and MCP subprocesses, not to the Bash tool that runs these scripts — so the
`SessionStart` hook also writes it to `.plugin-data-dir` at the plugin root,
and `paths.py` reads that when the variable is absent.

## Requirements

Python 3.10+ and `jinja2`, which only the digest email needs. An installed
plugin builds its own venv on first session. In this repo, `python3 -m venv
.venv && .venv/bin/pip install -r skills/running-coach/requirements.txt`.
Either way the digest scripts find that interpreter and re-exec into it, so
plain `python3` works.

## Status

Phase 3 complete: portable skill plus a Claude plugin wrapper, both validated
with `claude plugin validate`. See `ROADMAP.md`.

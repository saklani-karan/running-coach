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
├── .github/workflows/release.yml  builds and attaches the archive on a v* tag
└── package.py                     validates and zips the skill, into dist/
```

## Installing

The skill follows the Agent Skills spec — `SKILL.md` with `name`/`description`
frontmatter, the directory named to match — so every host below takes the same
skill, just packaged differently.

### Upload the archive (Cowork, claude.ai)

Download `running-coach.zip` from the
[latest release](https://github.com/saklani-karan/running-coach/releases/latest),
then **Customize → Skills → + → Create skill → Upload a skill**, and toggle it
on. Building it yourself is one command:

```bash
python3 package.py
```

Either way the archive holds a single `running-coach/` directory at its root —
an archive of loose files is rejected on upload. Hosts that expect the
`.skill` extension take the same archive renamed.

### Point an agent at the directory (Claude Code, OpenClaw, OpenCode)

Anything that scans a skills root picks the directory up in place, no
packaging step. Symlink or copy `skills/running-coach` into that root:

| Agent | Root |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| OpenClaw | `~/.agents/skills/`, `~/.openclaw/skills/`, or `<workspace>/skills/` |
| OpenCode | `~/.config/opencode/skills/`, or the `.claude` / `.agents` roots |

```bash
ln -s "$PWD/skills/running-coach" ~/.claude/skills/running-coach
```

### What it needs from the host

**Code execution**, since every capability is a Python script. On claude.ai
that is Settings → Capabilities → Code execution and file creation; Team and
Enterprise plans need it enabled at the organisation level first.

**Your own connectors.** The skill bundles none and holds no credentials — it
asks the agent to fetch through whatever Strava, Spotify and AccuWeather
connectors the session already has. Strava's is its official connector and
needs a Strava subscription. Without one the skill still loads and every
script runs, but `cache.py status` reports everything missing and there is no
data to work from, which looks like a broken skill and is not.

On a hosted sandbox the filesystem is per-session, so the cache starts empty
each time and is rebuilt from MCP rather than reused. Everything still works,
it just re-fetches. Locally the cache persists and `cache.py status` only asks
for what has gone stale.

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

## Packaging and releases

`python3 package.py --validate` checks the frontmatter against the Agent Skills
spec: the six allowed keys, a kebab-case `name` matching the directory, and a
`description` within limits. It also warns past 200 characters, which is
roughly where claude.ai's picker truncates. `python3 package.py` runs the same
checks and then writes the archive.

Releases are automated. Pushing a `v*` tag runs
[`.github/workflows/release.yml`](.github/workflows/release.yml), which builds
on Python 3.10 — the version the skill claims to support, so the claim stays
honest — then compiles every script, validates the frontmatter, smoke-tests
each entry point, and confirms `running-coach/SKILL.md` sits at the archive
root before publishing. Any of those failing means no release rather than a
broken one, so the download link above is always something that works:

```bash
git tag -a v1.1.0 -m "running-coach v1.1.0" && git push origin v1.1.0
```

Re-running the workflow by hand against an existing tag replaces the attached
archive, so a release can be rebuilt without moving the tag. `dist/` is
gitignored: the archive is a release asset, never a committed file.

## Sharing this skill

Point people at the
[latest release](https://github.com/saklani-karan/running-coach/releases/latest)
rather than the repo — they need the zip, not the source. Worth saying up
front that it needs code execution and their own Strava connector, since a
missing connector is the one thing that makes a correctly installed skill look
broken:

> I built a running-coach skill for Claude — it plans runs off your Strava
> history (pace, projected finish, effort band from your trailing load), builds
> a weekly digest email, and generates Spotify playlists sized to the run.
>
> Install: grab `running-coach.zip` from
> https://github.com/saklani-karan/running-coach/releases/latest then
> Customize → Skills → + → Create skill → Upload a skill, and toggle it on.
>
> Two things it needs:
> - Code execution enabled (Settings → Capabilities). It's all Python scripts.
> - Your own Strava connector, plus Spotify and AccuWeather if you want
>   playlists and weather. The skill has no credentials of its own — it asks
>   Claude to fetch through your connectors. Strava's is the official one and
>   needs a subscription.
>
> Then just ask normally: "plan my 10k for tomorrow morning", "build my weekly
> run digest", "make me a playlist for an 8k". No need to name the skill.

#!/usr/bin/env python3
"""
cache.py — the only gateway to the running-coach data cache.

The agent is the only thing that talks to MCP; this script is the only thing
that reads or writes the cache. Nothing here needs an access token.

    python3 scripts/cache.py status                  # what's cached, how stale
    python3 scripts/cache.py put <key> --file -      # ingest an MCP response
    python3 scripts/cache.py show <key>              # compact summary, not raw JSON
    python3 scripts/cache.py validate --for digest   # shape-check before a run

Refresh loop: `status` names the exact MCP call for anything missing or stale,
`put` ingests that call's response verbatim, `validate` confirms the result is
usable. Run `status` first; never hand-edit the JSON files.
"""
import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/
from common import normalize as nz
from common.paths import DATA_DIR, ensure_dirs

MANIFEST_NAME = "cache.json"

# Freshness budgets, in hours. Strava data for the current week changes with
# every run, so it is cheap to refetch and worth keeping tight. Anything
# describing completed weeks or the athlete only moves on a weekly cadence.
HOURS_PER_DAY = 24
HOURS_PER_WEEK = 7 * HOURS_PER_DAY


@dataclass
class CacheKey:
    name: str
    ttl_hours: float | None
    mcp: str
    normalize: Callable
    summary: Callable
    needed_by: tuple = ()
    per_activity: bool = False
    local: bool = False   # written by a flow, not fetched from MCP
    note: str = ""

    @property
    def filename(self):
        return f"{self.name}.json"


# ----------------------------------------------------------------- summaries
def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _summary_activities(v):
    if not v:
        return "no runs"
    km = sum(a["summary"]["distance"] for a in v) / 1000
    return (f"{_plural(len(v), 'run')} · {km:.1f} km · "
            f"{v[0]['start_local'][:10]} to {v[-1]['start_local'][:10]}")


def _summary_performance(v):
    efforts = {e["display_text"] for rec in v.values() for e in rec["best_efforts"]}
    ordered = sorted(efforts, key=lambda l: nz.EFFORT_METRES.get(l, 0))
    noun = "activity" if len(v) == 1 else "activities"
    return f"{len(v)} {noun} · best efforts: {', '.join(ordered) or 'none'}"


def _summary_streams(v):
    return " · ".join(f"{aid}: {len(rec['distance'])} samples" for aid, rec in v.items()) or "empty"


def _summary_weather(v):
    loc = (v.get("location") or {}).get("name", "unknown location")
    return f"{loc} · {len(v.get('per_run_conditions') or {})} runs with conditions"


def _summary_profile(v):
    return f"{v.get('firstname') or 'athlete'} · focus={v.get('current_focus') or 'unknown'}"


def _summary_zones(v):
    pb = v.get("personal_bests") or {}
    return ", ".join(f"{k} {t//60}:{t%60:02d}" for k, t in pb.items()) or "no personal bests"


def _summary_spotify(v):
    return f"{len(v.get('tracks') or [])} tracks · {len(v.get('artists') or [])} artists"


def _summary_playlist(v):
    return f"{v.get('name') or 'unnamed'} · {v.get('playlist_id') or 'no id yet'}"


# ---------------------------------------------------------------- key registry
KEYS = {k.name: k for k in [
    CacheKey(
        "activities_week", 6,
        "Strava:list_activities for the current Monday-to-Sunday week",
        lambda p, aid: nz.normalize_activities(p, want_polyline=True),
        _summary_activities, needed_by=("digest",),
    ),
    CacheKey(
        "activities_history", HOURS_PER_WEEK,
        "Strava:list_activities for the 5 complete weeks before this one",
        lambda p, aid: nz.normalize_activities(p, want_polyline=False),
        _summary_activities, needed_by=("digest", "planning"),
        note="the goal baseline is a mean over these weeks",
    ),
    CacheKey(
        "performance", 6,
        "Strava:get_activity_performance once per activity in activities_week",
        nz.normalize_performance, _summary_performance,
        needed_by=("digest", "planning", "playlist"), per_activity=True,
        note="best 1k/5k efforts drive every pace projection",
    ),
    CacheKey(
        "streams", 6,
        "Strava:get_activity_streams with keys=distance,altitude,velocity_smooth,time",
        nz.normalize_streams, _summary_streams,
        needed_by=("digest",), per_activity=True,
    ),
    CacheKey(
        "weather", HOURS_PER_WEEK,
        "AccuWeather:widgets-search-claude for the city, then "
        "AccuWeather:widgets-historical-claude for the run dates",
        lambda p, aid: nz.normalize_weather(p), _summary_weather,
        needed_by=("digest",),
        note="returns climatology, so the athlete's own run notes stay ground truth",
    ),
    CacheKey(
        "athlete_profile", HOURS_PER_WEEK,
        "Strava:get_athlete_profile",
        lambda p, aid: nz.normalize_athlete_profile(p), _summary_profile,
        note="current_focus selects the digest build multiplier",
    ),
    CacheKey(
        "athlete_zones", 4 * HOURS_PER_WEEK,
        "Strava:get_athlete_zones",
        lambda p, aid: nz.normalize_athlete_zones(p), _summary_zones,
        note="personal bests are the reference the digest compares against",
    ),
    CacheKey(
        "spotify_recent", HOURS_PER_DAY,
        'Spotify:search for "my recently played songs"',
        lambda p, aid: nz.normalize_spotify_recent(p), _summary_spotify,
    ),
    CacheKey(
        "playlist_state", None,
        "python3 scripts/cache.py put playlist_state --set playlist_id=<id> "
        "--set name=<name>",
        lambda p, aid: nz.normalize_playlist_state(p), _summary_playlist,
        local=True,
        note="the pinned run playlist; the playlist flow rewrites it each run",
    ),
]}

CAPABILITIES = ("digest", "planning", "playlist")


class CacheMissing(FileNotFoundError):
    """Raised when a required cache key is absent, with the fix in the message."""


# ------------------------------------------------------------------- storage
def _data_dir(override=None):
    return Path(override).expanduser() if override else DATA_DIR


def _manifest(data_dir):
    path = data_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        # A corrupt manifest only costs freshness metadata; fall back to mtimes.
        print(f"warning: {path} is not valid JSON, falling back to file times",
              file=sys.stderr)
        return {}


def _write_manifest(data_dir, manifest):
    (data_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _key(name):
    if name in KEYS:
        return KEYS[name]
    raise SystemExit(
        f"unknown cache key '{name}'. Known keys: {', '.join(KEYS)}"
    )


def load(name, data_dir=None, required=True, default=None):
    """Read a cached key. Raises CacheMissing with the refresh command."""
    key = _key(name)
    path = _data_dir(data_dir) / key.filename
    if not path.exists():
        if not required:
            return default
        raise CacheMissing(
            f"{path} is missing. Call {key.mcp}, then pipe the response into "
            f"`python3 scripts/cache.py put {key.name} --file -`."
        )
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise CacheMissing(
            f"{path} is not valid JSON ({exc}). Re-ingest it: call {key.mcp}, then "
            f"python3 scripts/cache.py put {key.name} --file -"
        ) from exc


def _age_hours(data_dir, key, manifest):
    entry = manifest.get(key.name)
    stamp = entry.get("updated") if isinstance(entry, dict) else None
    if stamp:
        try:
            updated = datetime.fromisoformat(stamp)
        except ValueError:
            stamp = None
    if not stamp:
        path = data_dir / key.filename
        if not path.exists():
            return None
        updated = datetime.fromtimestamp(path.stat().st_mtime)
    if updated.tzinfo:
        updated = updated.astimezone().replace(tzinfo=None)
    return (datetime.now() - updated).total_seconds() / 3600


def _fmt_age(hours):
    if hours is None:
        return "-"
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < HOURS_PER_DAY:
        return f"{hours:.0f}h"
    return f"{hours / HOURS_PER_DAY:.0f}d"


def _state(data_dir, key, manifest):
    if not (data_dir / key.filename).exists():
        return "missing"
    age = _age_hours(data_dir, key, manifest)
    if key.ttl_hours is None or age is None:
        return "fresh"
    return "stale" if age > key.ttl_hours else "fresh"


def inventory(data_dir):
    manifest = _manifest(data_dir)
    rows = []
    for key in KEYS.values():
        state = _state(data_dir, key, manifest)
        age = _age_hours(data_dir, key, manifest)
        entry = manifest.get(key.name) or {}
        rows.append({
            "key": key.name,
            "state": state,
            "age_hours": round(age, 2) if age is not None else None,
            "ttl_hours": key.ttl_hours,
            "mcp": key.mcp,
            "local": key.local,
            "needed_by": list(key.needed_by),
            "summary": entry.get("summary", ""),
        })
    return rows


# ------------------------------------------------------------------ commands
def cmd_status(args):
    data_dir = _data_dir(args.data)
    rows = inventory(data_dir)
    if args.json:
        print(json.dumps({"data_dir": str(data_dir), "keys": rows}, indent=2))
        return 0

    print(f"cache: {data_dir}")
    print(f"{'KEY':<20} {'STATE':<8} {'AGE':>5}  NOTES")
    for r in rows:
        if r["needed_by"]:
            fallback = f"needed by {', '.join(r['needed_by'])}"
        else:
            fallback = "local state" if r["local"] else "optional"
        print(f"{r['key']:<20} {r['state']:<8} {_fmt_age(r['age_hours']):>5}  "
              f"{r['summary'] or fallback}")

    refresh = [r for r in rows if r["state"] != "fresh" and not r["local"]]
    if not refresh:
        print("\nAll MCP-backed keys fresh — go straight to the capability script.")
        return 0
    print(f"\n{len(refresh)} key(s) to refresh:")
    for r in refresh:
        print(f"  {r['key']} ({r['state']}, {', '.join(r['needed_by']) or 'optional'})")
        print(f"    1. call {r['mcp']}")
        print(f"    2. python3 scripts/cache.py put {r['key']} --file -")
    print("\nA stale key still loads; refresh it only if the answer should be current.")
    return 0


def _read_payload(args):
    if args.set:
        payload = {}
        for pair in args.set:
            if "=" not in pair:
                raise SystemExit(f"--set expects key=value, got '{pair}'")
            k, v = pair.split("=", 1)
            payload[k.strip()] = v.strip()
        return payload
    if args.file in (None, "-"):
        if sys.stdin.isatty():
            raise SystemExit(
                "nothing on stdin. Pipe the MCP response in "
                f"(python3 scripts/cache.py put {args.key} --file response.json), "
                "or use --set key=value for simple objects."
            )
        raw = sys.stdin.read()
    else:
        path = Path(args.file).expanduser()
        if not path.exists():
            raise SystemExit(f"{path} does not exist")
        raw = path.read_text()
    if not raw.strip():
        raise SystemExit("the payload was empty — nothing to cache")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"the payload is not valid JSON ({exc}). Pass the MCP tool response "
            "verbatim, with no surrounding prose or markdown fences."
        ) from exc


def cmd_put(args):
    key = _key(args.key)
    data_dir = _data_dir(args.data)
    ensure_dirs(data_dir)
    payload = _read_payload(args)

    try:
        value = key.normalize(payload, args.activity_id)
    except nz.NormalizeError as exc:
        print(f"error: {key.name} — {exc}", file=sys.stderr)
        return 1

    path = data_dir / key.filename
    if key.per_activity and not args.replace:
        existing = load(key.name, data_dir, required=False, default={}) or {}
        merged = dict(existing)
        merged.update(value)
        value = merged

    path.write_text(json.dumps(value, indent=2) + "\n")
    summary = key.summary(value)
    manifest = _manifest(data_dir)
    manifest[key.name] = {
        "updated": datetime.now().replace(microsecond=0).isoformat(),
        "source": key.mcp,
        "summary": summary,
    }
    _write_manifest(data_dir, manifest)
    print(f"cached {key.name}: {summary}")
    return 0


def cmd_show(args):
    key = _key(args.key)
    data_dir = _data_dir(args.data)
    try:
        value = load(key.name, data_dir)
    except CacheMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.raw:
        print(json.dumps(value, indent=2))
        return 0
    manifest = _manifest(data_dir)
    age = _age_hours(data_dir, key, manifest)
    if args.json:
        print(json.dumps({
            "key": key.name,
            "state": _state(data_dir, key, manifest),
            "age_hours": round(age, 2) if age is not None else None,
            "ttl_hours": key.ttl_hours,
            "source": key.mcp,
            "note": key.note,
            "summary": key.summary(value),
        }, indent=2))
        return 0
    print(f"{key.name}  ({_state(data_dir, key, manifest)}, {_fmt_age(age)} old)")
    print(f"  source : {key.mcp}")
    if key.note:
        print(f"  note   : {key.note}")
    print(f"  content: {key.summary(value)}")
    return 0


def cmd_validate(args):
    data_dir = _data_dir(args.data)
    capabilities = [args.capability] if args.capability else list(CAPABILITIES)
    required = [k for k in KEYS.values()
                if any(c in k.needed_by for c in capabilities)]

    problems, warnings, loaded = [], [], {}
    for key in required:
        try:
            value = load(key.name, data_dir)
        except CacheMissing as exc:
            problems.append(str(exc))
            continue
        try:
            loaded[key.name] = key.normalize(value, None)
        except nz.NormalizeError as exc:
            problems.append(
                f"{key.filename} failed its shape check: {exc} "
                f"Re-ingest with: call {key.mcp}, then "
                f"python3 scripts/cache.py put {key.name} --file -"
            )

    # Cross-checks: per-activity keys must cover every run in the week.
    week = loaded.get("activities_week") or []
    for name in ("performance", "streams"):
        if name not in loaded:
            continue
        gaps = [a["id"] for a in week if a["id"] not in loaded[name]]
        if gaps:
            problems.append(
                f"{name}.json has no entry for activity {', '.join(gaps)} "
                f"(present: {', '.join(loaded[name]) or 'none'}). For each missing "
                f"id call {KEYS[name].mcp.split()[0]}"
                f"{' with keys=distance,altitude,velocity_smooth,time' if name == 'streams' else ''}"
                f", then python3 scripts/cache.py put {name} --activity-id <id> --file -"
            )
    # Soft gaps: the digest degrades gracefully rather than failing.
    if "weather" in loaded and week:
        conditions = loaded["weather"].get("per_run_conditions") or {}
        gaps = [a["id"] for a in week if a["id"] not in conditions]
        if gaps:
            warnings.append(
                f"weather.json has no per_run_conditions for activity "
                f"{', '.join(gaps)}; those runs render without a conditions line. "
                "Call AccuWeather:widgets-historical-claude for their dates to fill it."
            )

    label = ", ".join(capabilities)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s) blocking {label}:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"cache OK for {label} ({len(required)} keys checked in {data_dir})")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `status` before any capability script; it names the MCP call "
               "for anything missing.",
    )
    ap.add_argument("--data", help=f"cache directory (default {DATA_DIR})")
    sub = ap.add_subparsers(dest="command", required=True)

    # --data is accepted on either side of the subcommand, because both
    # `cache.py --data DIR status` and `cache.py status --data DIR` read
    # naturally and guessing wrong should not be an argparse error. SUPPRESS
    # keeps the subcommand's default from clobbering a value given up front.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--data", default=argparse.SUPPRESS,
                        help="cache directory (may also precede the subcommand)")

    p = sub.add_parser("status", parents=[shared],
                       help="what is cached, how old, what to refresh")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("put", parents=[shared],
                       help="ingest an MCP tool response into the cache")
    p.add_argument("key", choices=list(KEYS))
    p.add_argument("--file", help="JSON file, or - for stdin (default)")
    p.add_argument("--activity-id", help="activity id for performance/streams payloads")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="build the payload inline instead of piping JSON")
    p.add_argument("--replace", action="store_true",
                   help="overwrite per-activity keys instead of merging")
    p.set_defaults(func=cmd_put)

    p = sub.add_parser("show", parents=[shared],
                       help="compact summary of a cached key")
    p.add_argument("key", choices=list(KEYS))
    p.add_argument("--json", action="store_true",
                   help="the summary as JSON, rather than as lines")
    p.add_argument("--raw", action="store_true",
                   help="dump the cached JSON itself instead of a summary")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("validate", parents=[shared],
                       help="shape-check the cache before a run")
    p.add_argument("--for", dest="capability", choices=CAPABILITIES,
                   help="only check what this capability needs")
    p.set_defaults(func=cmd_validate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

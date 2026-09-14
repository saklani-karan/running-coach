#!/usr/bin/env python3
"""
build_playlist.py — turn a run plan into a Spotify generate_playlist prompt.

Does the deterministic part: size the playlist to the projected finish time,
seed it from cached recent listening, and print the exact Spotify MCP call
sequence. Creating, saving and deleting playlists is the agent's job through
the Spotify tools — see references/run-playlist.md.

Usage:
    python3 scripts/playlist/build_playlist.py 5
    python3 scripts/playlist/build_playlist.py 10 --genre "Hindi indie" --json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
import cache
from common.pace import fmt_hms
from common.paths import DATA_DIR
from planning.plan_run import plan

DEFAULT_GENRE = "Hindi indie / pop"
DEFAULT_PLAYLIST_NAME = "Today Run's Playlist"

# Seed with a handful of artists: enough to fix the vibe, few enough that the
# generator still reaches beyond what the athlete already has on repeat.
MAX_SEED_ARTISTS = 6

# The closing stretch is where the playlist has to do actual work, so the arc
# reserves the last five minutes for the highest-tempo tracks.
FINISH_KICK_MINUTES = 5


def seed_artists(data_dir=None):
    recent = cache.load("spotify_recent", data_dir, required=False, default={}) or {}
    return (recent.get("artists") or [])[:MAX_SEED_ARTISTS]


def build_prompt(distance_km, finish_sec, band, genre=DEFAULT_GENRE, artists=None):
    minutes = round(finish_sec / 60)
    seed = ", ".join(artists) if artists else "the athlete's usual favourites"
    return (
        f"A {minutes}-minute running playlist, sized for {distance_km:g} km at "
        f"{band} effort. Style: {genre}, seeded from {seed}. "
        f"Energy arc: an easy warm-up, a strong sustained middle, and a lifting, "
        f"high-tempo final {FINISH_KICK_MINUTES} minutes for a strong finish. "
        "No slow ballads."
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Size a run playlist to the projected finish and build its prompt.")
    ap.add_argument("distance_km", type=float, help="planned distance in km")
    ap.add_argument("--data", help=f"cache directory (default {DATA_DIR})")
    ap.add_argument("--genre", default=DEFAULT_GENRE)
    ap.add_argument("--band", choices=("easy", "steady", "hard"),
                    help="override the recommended effort band")
    ap.add_argument("--minutes", type=float,
                    help="set the duration directly, skipping the pace projection")
    ap.add_argument("--json", action="store_true",
                    help="print {duration, prompt, mcp_steps} as JSON")
    args = ap.parse_args(argv)

    if args.distance_km <= 0:
        print("error: distance_km must be positive", file=sys.stderr)
        return 1

    band = args.band or "steady"
    if args.minutes:
        finish_sec = args.minutes * 60
    else:
        try:
            result = plan(args.distance_km, args.data, band=args.band)
        except cache.CacheMissing as exc:
            print(f"error: {exc}\nOr pass --minutes to skip the projection "
                  "entirely.", file=sys.stderr)
            return 1
        finish_sec = result["recommended"]["finish_sec"]
        band = result["recommended"]["band"]

    artists = seed_artists(args.data)
    prompt = build_prompt(args.distance_km, finish_sec, band, args.genre, artists)
    pinned = cache.load("playlist_state", args.data, required=False, default={}) or {}

    playlist_name = pinned.get("name") or DEFAULT_PLAYLIST_NAME
    steps = []
    if pinned.get("playlist_id"):
        steps.append(f"Spotify:remove_from_library on {pinned['playlist_id']} "
                     "(keeps one run playlist in the library)")
    else:
        steps.append("no pinned playlist cached, so nothing to remove first")
    steps += [
        "Spotify:generate_playlist with the prompt above",
        "Spotify:save_to_library on the new playlist URI",
        "python3 scripts/cache.py put playlist_state --set playlist_id=<new id> "
        f'--set "name={playlist_name}"',
    ]

    payload = {
        "distance_km": args.distance_km,
        "band": band,
        "duration": fmt_hms(finish_sec),
        "duration_minutes": round(finish_sec / 60),
        "seed_artists": artists,
        "pinned_playlist": pinned.get("playlist_id") or None,
        "prompt": prompt,
        "mcp_steps": steps,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Target duration : {payload['duration']} "
          f"(~{payload['duration_minutes']} min, {band} effort)")
    if not artists:
        print("Seed artists    : none cached — call Spotify:search for "
              '"my recently played songs" and cache it as spotify_recent')
    else:
        print(f"Seed artists    : {', '.join(artists)}")
    print(f"\ngenerate_playlist prompt:\n\n{prompt}\n")
    print("Then, via the Spotify MCP:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

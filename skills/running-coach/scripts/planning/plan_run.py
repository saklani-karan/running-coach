#!/usr/bin/env python3
"""
plan_run.py — project today's run: current fitness -> pace, finish, effort band.

Anchors on the most recent best effort in the cache (current fitness), projects
the planned distance with Riegel, then recommends an effort band from the last
seven days of load. The projected finish is what the playlist is sized to and
what the digest's next-week plan quotes.

Usage:
    python3 scripts/planning/plan_run.py 5
    python3 scripts/planning/plan_run.py 10 --band easy --json
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
import cache
from common.normalize import EFFORT_METRES
from common.pace import fmt_hms, fmt_pace, riegel
from common.paths import DATA_DIR
from common.weeks import parse_dt

# Riegel's exponent: 1.06 is the standard endurance value, fitted across race
# distances from 1500 m to the marathon. Raise it if long projections read fast.
DEFAULT_EXPONENT = 1.06

# Riegel projects a *race* effort. Training runs are slower by band; these
# multipliers put easy pace roughly 60-90 s/km above race pace, the usual
# easy-running prescription, with steady between the two.
BAND_MULTIPLIERS = {"hard": 1.02, "steady": 1.10, "easy": 1.20}

# Rested enough for quality work: three days is long enough for a hard session
# to have cleared, and a week under 80% of target leaves room to add intensity.
# Measured in hours so a run 13 hours ago doesn't round to "a day of rest".
RESTED_HOURS = 3 * 24
UNDER_LOADED_RATIO = 0.80
# Above 110% of the weekly target the priority is recovery, not another effort.
OVER_LOADED_RATIO = 1.10

LOAD_WINDOW_DAYS = 7


def recent_efforts(data_dir=None):
    """{distance_m: seconds} from the cached recent performance data."""
    performance = cache.load("performance", data_dir, required=False, default={}) or {}
    best = {}
    for record in performance.values():
        for e in record.get("best_efforts", []):
            metres = EFFORT_METRES.get(e["display_text"])
            if metres and (metres not in best or e["value"] < best[metres]):
                best[metres] = e["value"]
    return best


def personal_bests(data_dir=None):
    """{distance_m: seconds} from the cached athlete zones."""
    zones = cache.load("athlete_zones", data_dir, required=False, default={}) or {}
    return {EFFORT_METRES[label]: seconds
            for label, seconds in (zones.get("personal_bests") or {}).items()
            if label in EFFORT_METRES}


def project(distance_km, efforts, exponent=DEFAULT_EXPONENT):
    """Riegel-project distance_km from the closest effort at or below it."""
    target_m = distance_km * 1000
    at_or_below = [m for m in efforts if m <= target_m]
    anchor = max(at_or_below) if at_or_below else min(efforts)
    finish = riegel(efforts[anchor], anchor, target_m, exponent)
    return finish, finish / distance_km, anchor


def recent_load(data_dir=None):
    """(km in the last LOAD_WINDOW_DAYS, hours since the last run) from the cache.

    The window is a rolling one ending now, NOT the Monday-to-Sunday week the
    digest reports — the two legitimately quote different mileage. Both are
    measured against the clock, so a stale cache reads as more rest than the
    athlete actually had; `cache.py status` flags that before it matters.
    """
    activities = []
    for key in ("activities_week", "activities_history"):
        activities += cache.load(key, data_dir, required=False, default=[]) or []
    if not activities:
        return None, None
    dates = [parse_dt(a["start_local"]) for a in activities]
    cutoff = datetime.now() - timedelta(days=LOAD_WINDOW_DAYS)
    km = sum(a["summary"]["distance"]
             for a, d in zip(activities, dates) if d > cutoff) / 1000
    hours_since = max((datetime.now() - max(dates)).total_seconds() / 3600, 0)
    return km, hours_since


def fmt_rest(hours):
    """Rest as the unit a runner would use for it, always rounded down.

    Flooring keeps every line quoting the same figure, and errs towards
    understating rest — the safe direction for a rule that unlocks hard efforts.
    """
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


def recommend_band(load_km, hours_since, weekly_goal_km):
    """Pick an effort band from recent load, with the reasoning attached."""
    if load_km is None or not weekly_goal_km:
        return "steady", ("no trailing load cached, so defaulting to steady — "
                          "refresh activities_history for a real recommendation")
    ratio = load_km / weekly_goal_km
    window = f"{load_km:.0f} km in the last {LOAD_WINDOW_DAYS} rolling days"
    if ratio >= OVER_LOADED_RATIO:
        return "easy", (f"{window} is {ratio:.0%} of the "
                        f"{weekly_goal_km:.0f} km target — bank the volume, "
                        "skip the intensity")
    if hours_since is not None and hours_since >= RESTED_HOURS and ratio < UNDER_LOADED_RATIO:
        return "hard", (f"{fmt_rest(hours_since)} since the last run and only "
                        f"{window.lower()} ({ratio:.0%} of target) — rested "
                        "enough for a quality session")
    rest = (f", and only {fmt_rest(hours_since)} since the last run"
            if hours_since is not None and hours_since < RESTED_HOURS else "")
    return "steady", (f"{window} ({ratio:.0%} of target){rest} — hold the rhythm")


def weekly_goal_km(data_dir=None):
    """The rolling weekly distance target, reusing the digest's goal model."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "digest"))
    import analyze  # noqa: E402  (deferred: only planning needs the goal model)
    history = cache.load("activities_history", data_dir, required=False, default=[]) or []
    if not history:
        return None
    weeks = analyze.aggregate_weeks(history)
    window = sorted(weeks, reverse=True)[:analyze.TRAILING_WEEKS]
    if not window:
        return None
    profile = cache.load("athlete_profile", data_dir, required=False, default={})
    _focus, multiplier = analyze.resolve_focus(profile)
    baseline = sum(weeks[k]["distance"] for k in window) / len(window)
    return baseline * multiplier / 1000


def plan(distance_km, data_dir=None, exponent=DEFAULT_EXPONENT, band=None,
         start_in_hours=0):
    efforts, source = recent_efforts(data_dir), "recent runs"
    if not efforts:
        efforts, source = personal_bests(data_dir), "personal bests"
    if not efforts:
        raise cache.CacheMissing(
            "no best efforts cached, so there is nothing to project from. Call "
            "Strava:get_activity_performance for a recent run, then "
            "`python3 scripts/cache.py put performance --activity-id <id> --file -` "
            "(or Strava:get_athlete_zones for personal bests)."
        )

    finish, race_pace, anchor = project(distance_km, efforts, exponent)
    goal_km = weekly_goal_km(data_dir)
    load_km, hours_since = recent_load(data_dir)
    if hours_since is not None:
        # Rest accrues until the run actually starts, so an evening run planned
        # in the morning gets credit for the hours in between. Rounded once,
        # here, so every line downstream quotes the same number.
        hours_since = round(hours_since + start_in_hours, 1)
    starts_at = datetime.now() + timedelta(hours=start_in_hours)
    chosen, reason = recommend_band(load_km, hours_since, goal_km)
    if band:
        chosen, reason = band, "requested explicitly"

    target_pace = race_pace * BAND_MULTIPLIERS[chosen]
    return {
        "distance_km": distance_km,
        "starts_at": starts_at.isoformat(timespec="minutes"),
        "starts_in_hours": start_in_hours,
        "anchor": {"distance_km": anchor / 1000, "time": fmt_hms(efforts[anchor]),
                   "pace": fmt_pace(efforts[anchor] / (anchor / 1000)),
                   "source": source},
        "race_effort": {"finish": fmt_hms(finish), "finish_sec": round(finish),
                        "pace": fmt_pace(race_pace), "riegel_exponent": exponent},
        "recommended": {
            "band": chosen, "reason": reason,
            "pace": fmt_pace(target_pace),
            "finish": fmt_hms(target_pace * distance_km),
            "finish_sec": round(target_pace * distance_km),
        },
        "load": {
            "rolling_7_day_km": round(load_km, 1) if load_km is not None else None,
            "weekly_target_km": round(goal_km, 1) if goal_km else None,
            "hours_since_last_run": hours_since,
            "window": f"rolling {LOAD_WINDOW_DAYS} days ending now, not the "
                      "Monday-to-Sunday week the digest reports",
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Project pace, finish time and effort band for a planned run.")
    ap.add_argument("distance_km", type=float, help="planned distance in km")
    ap.add_argument("--data", help=f"cache directory (default {DATA_DIR})")
    ap.add_argument("--exp", type=float, default=DEFAULT_EXPONENT,
                    dest="exponent", help=f"Riegel exponent (default {DEFAULT_EXPONENT})")
    ap.add_argument("--band", choices=sorted(BAND_MULTIPLIERS),
                    help="override the recommended effort band")
    ap.add_argument("--start-in", type=float, default=0, metavar="HOURS",
                    help="hours from now until the run starts, so rest is "
                         "counted to then (e.g. 10 for an evening run planned "
                         "in the morning)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)

    if args.distance_km <= 0:
        print("error: distance_km must be positive", file=sys.stderr)
        return 1
    if args.start_in < 0:
        print("error: --start-in cannot be negative", file=sys.stderr)
        return 1
    try:
        result = plan(args.distance_km, args.data, args.exponent, args.band,
                      args.start_in)
    except cache.CacheMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    a, race, rec, load = (result["anchor"], result["race_effort"],
                          result["recommended"], result["load"])
    when = (f" starting about {result['starts_at'][11:]}"
            if result["starts_in_hours"] else "")
    print(f"Planned distance : {result['distance_km']:.1f} km{when}")
    print(f"Anchor effort    : {a['time']} over {a['distance_km']:.1f} km "
          f"({a['pace']}, from {a['source']})")
    print(f"Race effort      : {race['finish']} @ {race['pace']} "
          f"(Riegel exp {race['riegel_exponent']})")
    print(f"Recommended      : {rec['band']} — {rec['finish']} @ {rec['pace']}")
    print(f"                   {rec['reason']}")
    if load["rolling_7_day_km"] is not None:
        print(f"Trailing load    : {load['rolling_7_day_km']} km over the last "
              f"{LOAD_WINDOW_DAYS} rolling days"
              + (f", target {load['weekly_target_km']} km/wk"
                 if load["weekly_target_km"] else "")
              + f"; {fmt_rest(load['hours_since_last_run'])} of rest"
              + (f" by the time you start (+{result['starts_in_hours']:g}h from now)"
                 if result["starts_in_hours"] else " as of now"))
        print("                   (rolling window — the digest counts the "
              "Monday-to-Sunday week, so its mileage differs)")
        if not result["starts_in_hours"]:
            print("                   running later today? add --start-in "
                  "<hours> so the rest counts to then")
    km, start_in = result["distance_km"], result["starts_in_hours"]
    carry = f" --start-in {start_in:g}" if start_in else ""
    print(f"\nNext: playlist  → python3 scripts/playlist/build_playlist.py {km:g}")
    print(f"      digest     → python3 scripts/digest/run_pipeline.py "
          f"--plan-km {km:g}{carry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

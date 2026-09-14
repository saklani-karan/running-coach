#!/usr/bin/env python3
"""
analyze.py — weekly running analytics for the digest.

Reads the cache (see scripts/cache.py) and writes digest_data.json: weekly
totals, a rolling API-derived goal, goal comparison, per-run detail, the cities
run in, and the insights that survived their own tests.

Goal model — no hardcoded target, it moves with the athlete:
    baseline  = mean weekly distance over the last TRAILING_WEEKS complete weeks
    goal_dist = baseline * the multiplier for the athlete's current focus
    goal_runs = median runs/week over the same window

Usage:
    python3 scripts/digest/analyze.py
    python3 scripts/digest/analyze.py --json          # digest_data.json to stdout
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
import cache
import insights as insight_lib
from common.pace import fmt_hms, fmt_pace, speed_to_pace
from common.paths import DATA_DIR, OUTPUT_DIR, ensure_dirs
from common.weeks import parse_dt, week_key

# Four weeks is the shortest window that averages out a single missed or
# doubled week while still tracking a training block that changes monthly.
TRAILING_WEEKS = 4

# Strava's current_focus maps onto how much the week should grow over the
# trailing baseline. A build block adds 10% — the classic progressive-overload
# step that stays under the 10%-per-week injury heuristic.
FOCUS_MULTIPLIERS = {"build": 1.10, "peak": 1.15, "maintain": 1.00, "recover": 0.80}
DEFAULT_FOCUS = "build"

# Strava reports no weekly run-count target either; the median of the trailing
# window is the athlete's own revealed habit.
FALLBACK_GOAL_RUNS = 5


def aggregate_weeks(activities):
    """Monday-anchored weekly totals for a list of activity records."""
    weeks = {}
    for a in activities:
        key = week_key(parse_dt(a["start_local"]))
        w = weeks.setdefault(key, {"distance": 0.0, "time": 0, "runs": 0,
                                   "elev": 0.0, "cal": 0})
        s = a["summary"]
        w["distance"] += s["distance"]
        w["time"] += s.get("moving_time", 0)
        w["runs"] += 1
        w["elev"] += s.get("elevation_gain", 0) or 0
        w["cal"] += s.get("total_calories", 0) or 0
    return weeks


def km_splits(streams):
    """Per-completed-kilometre pace from the distance/time streams."""
    if not streams:
        return []
    distance, time = streams.get("distance", []), streams.get("time", [])
    out = []
    prev_d = prev_t = 0
    target = 1000
    for d, t in zip(distance, time):
        if d >= target:
            pace = (t - prev_t) / ((d - prev_d) / 1000) if d > prev_d else 0
            out.append({"km": target // 1000, "pace_sec": round(pace)})
            prev_d, prev_t = d, t
            target += 1000
    return out


def resolve_focus(profile):
    """The athlete's focus and its build multiplier, defaulting explicitly."""
    focus = ((profile or {}).get("current_focus") or "").strip().lower()
    if focus in FOCUS_MULTIPLIERS:
        return focus, FOCUS_MULTIPLIERS[focus]
    return DEFAULT_FOCUS, FOCUS_MULTIPLIERS[DEFAULT_FOCUS]


def build_next_week_plan(goal_dist_km, goal_runs, baseline_km, planned=None,
                         week_distance_km=0, week_open=False):
    per_run = goal_dist_km / goal_runs if goal_runs else goal_dist_km
    items = []
    if planned:
        rec = planned["recommended"]
        km = planned["distance_km"]
        effort = f"{rec['band']} effort ({rec['pace']}, about {rec['finish']})"
        # Name the clock time when one was given, so the line still reads
        # correctly to someone opening the email that evening.
        at = (f" around {planned['starts_at'][11:]}"
              if planned.get("starts_in_hours") else "")
        if week_open:
            # The reported week hasn't closed yet, so a run planned for today
            # lands inside it — crediting it to next week would double-count.
            after = week_distance_km + km
            items.append(
                f"Still to come before the week closes: {km:g} km{at} at "
                f"{effort}, which would take this week to {after:.1f} km"
                + (f", {after / goal_dist_km:.0%} of target." if goal_dist_km else ".")
            )
        else:
            share = f"{km / goal_dist_km:.0%}" if goal_dist_km else "a chunk"
            items.append(
                f"Already on the board: {km:g} km{at} at {effort} — "
                f"{share} of next week's distance in one run."
            )
    # When a this-week run leads the list, the rest of it needs saying out loud
    # that it is about next week — on a Sunday both weeks are live at once.
    lead = "Next week, get" if items else "Get"
    return {
        "headline": f"Reclaim the rhythm: {goal_runs} runs, {goal_dist_km:.0f} km",
        "target_km": round(goal_dist_km),
        "target_runs": goal_runs,
        "planned_run": planned,
        "items": items + [
            f"{lead} back to {goal_runs} sessions — consistency is the goal you "
            "actually missed, not speed.",
            f"Keep {per_run:.0f} km as the default run length; stack one longer "
            f"{per_run * 2:.0f} km effort mid-week.",
            "Hold easy pace. Save the personal-best gears for one strides "
            "session, not the daily grind.",
            "Front-load the week — bank runs early so a bad-weather Thursday "
            "can't sink the total.",
            f"Stretch goal: nudge past {goal_dist_km:.0f} km to keep the build "
            f"trending up from your {baseline_km:.0f} km baseline.",
        ],
    }


def load_planned_run(plan_path=None, plan_km=None, data_dir=None, start_in=0):
    """The planned run that leads the next-week section, if there is one.

    Accepts `plan_run.py --json` output on a path or stdin, or a bare distance
    to project here — the same model either way.
    """
    if plan_path:
        raw = sys.stdin.read() if plan_path == "-" else Path(plan_path).read_text()
        try:
            planned = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise cache.CacheMissing(
                f"--plan input is not valid JSON ({exc}). Produce it with "
                "`python3 scripts/planning/plan_run.py <km> --json`."
            ) from exc
        if "recommended" not in planned or "distance_km" not in planned:
            raise cache.CacheMissing(
                "--plan input is not a run plan. Expected the output of "
                "`python3 scripts/planning/plan_run.py <km> --json`."
            )
        return planned
    if plan_km:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "planning"))
        from plan_run import plan  # noqa: E402  (only needed with --plan-km)
        return plan(plan_km, data_dir, start_in_hours=start_in)
    return None


def analyze(data_dir=None, notes=None, planned=None):
    """Build the digest payload from the cache. Raises cache.CacheMissing."""
    notes = notes if notes is not None else []
    week = cache.load("activities_week", data_dir)
    history = cache.load("activities_history", data_dir)
    performance = cache.load("performance", data_dir)
    streams = cache.load("streams", data_dir)
    weather = cache.load("weather", data_dir)
    profile = cache.load("athlete_profile", data_dir, required=False, default={})
    zones = cache.load("athlete_zones", data_dir, required=False, default={})

    if not week:
        raise cache.CacheMissing(
            "activities_week.json holds no runs, so there is no week to write about. "
            "Confirm the date range passed to Strava:list_activities covers this "
            "Monday to today, then re-run `cache.py put activities_week`."
        )
    if not profile:
        notes.append("athlete_profile not cached — assuming a "
                     f"'{DEFAULT_FOCUS}' focus. Call Strava:get_athlete_profile "
                     "for the real one.")
    if not zones:
        notes.append("athlete_zones not cached — skipping the personal-best "
                     "comparison. Call Strava:get_athlete_zones to include it.")

    week = sorted(week, key=lambda a: a["start_local"])
    this_week_start = week_key(parse_dt(week[0]["start_local"]))
    week_end = this_week_start + timedelta(days=6)
    # Sunday is the natural day to write a weekly recap, and on Sunday the week
    # has not closed: a run still to come today belongs to this week, not next.
    # days_remaining counts whole days *after* today, so it is 0 on the Sunday.
    today = datetime.now().date()
    week_open = today <= week_end
    days_remaining = max((week_end - today).days, 0)

    totals_dist = sum(a["summary"]["distance"] for a in week)
    totals_time = sum(a["summary"]["moving_time"] for a in week)
    totals_elev = sum(a["summary"].get("elevation_gain", 0) or 0 for a in week)
    totals_cal = sum(a["summary"].get("total_calories", 0) or 0 for a in week)

    # ---- rolling goal from the trailing complete weeks ----
    history_weeks = aggregate_weeks(history)
    completed = sorted([k for k in history_weeks if k < this_week_start], reverse=True)
    window = completed[:TRAILING_WEEKS]
    if not window:
        notes.append("no complete trailing weeks in activities_history — the goal "
                     "falls back to this week's own distance, which makes the "
                     "comparison meaningless. Widen the Strava:list_activities range.")
    window_dist = [history_weeks[k]["distance"] for k in window]
    window_runs = [history_weeks[k]["runs"] for k in window]

    focus, multiplier = resolve_focus(profile)
    baseline = statistics.mean(window_dist) if window_dist else totals_dist
    goal_dist = baseline * multiplier
    goal_runs = int(round(statistics.median(window_runs))) if window_runs else FALLBACK_GOAL_RUNS
    dist_pct = totals_dist / goal_dist * 100 if goal_dist else 0
    runs_pct = len(week) / goal_runs * 100 if goal_runs else 0

    # ---- per-run detail ----
    conditions = weather.get("per_run_conditions") or {}
    runs = []
    for a in week:
        s, aid = a["summary"], a["id"]
        efforts = {e["display_text"]: e["value"]
                   for e in performance.get(aid, {}).get("best_efforts", [])}
        started = parse_dt(a["start_local"])
        description = a.get("description") or ""
        lines = description.split("\n")
        cond = conditions.get(aid, {})
        avg_pace = speed_to_pace(s["avg_speed"])
        runs.append({
            "id": aid,
            "name": a.get("name", "Run"),
            "label": lines[0] if lines else a.get("name", "Run"),
            "note": lines[1] if len(lines) > 1 else "",
            "city": a.get("location_summary") or "",
            "date": started.strftime("%a %d %b"),
            "time_local": started.strftime("%H:%M"),
            "distance_km": round(s["distance"] / 1000, 2),
            "moving_time": fmt_hms(s["moving_time"]),
            "avg_pace": fmt_pace(avg_pace),
            "avg_pace_sec": avg_pace,
            "elev": round(s.get("elevation_gain", 0) or 0),
            "calories": s.get("total_calories", 0),
            "best_1k": fmt_hms(efforts["1K"]) if "1K" in efforts else None,
            "best_5k": fmt_hms(efforts["5K"]) if "5K" in efforts else None,
            "weather": cond.get("inferred", ""),
            "weather_note": cond.get("logged_note", ""),
            "km_splits": km_splits(streams.get(aid, {})),
        })

    # ---- cities run in (this replaced the route map) ----
    by_place = {}
    for a in week:
        place = (a.get("location_summary") or "Unknown").strip()
        entry = by_place.setdefault(place, {"place": place, "runs": 0, "dist": 0.0})
        entry["runs"] += 1
        entry["dist"] += a["summary"]["distance"]
    cities = []
    for v in sorted(by_place.values(), key=lambda x: -x["dist"]):
        name, _, region = v["place"].partition(",")
        cities.append({"name": name.strip(), "region": region.strip(),
                       "runs": v["runs"], "distance_km": round(v["dist"] / 1000, 2)})

    ctx = insight_lib.DigestContext(
        week=week, history=history, performance=performance, streams=streams,
        runs=runs, history_weeks={k: history_weeks[k] for k in completed},
        goal_runs=goal_runs, personal_bests=(zones or {}).get("personal_bests", {}),
    )

    return {
        "generated": datetime.now().strftime("%d %b %Y"),
        "week_start": this_week_start.strftime("%d %b"),
        "week_end": week_end.strftime("%d %b %Y"),
        "week_in_progress": week_open,
        "days_remaining": days_remaining,
        "athlete": (profile or {}).get("firstname") or "Karan",
        "totals": {
            "distance_km": round(totals_dist / 1000, 2),
            "runs": len(week),
            "moving_time": fmt_hms(totals_time),
            "elevation_m": round(totals_elev),
            "calories": totals_cal,
            "avg_pace": (fmt_pace(speed_to_pace(totals_dist / totals_time))
                         if totals_time else "-"),
        },
        "goal": {
            "source": (f"Rolling {TRAILING_WEEKS}-week target "
                       f"(focus={focus}, x{multiplier})"),
            "focus": focus,
            "baseline_km": round(baseline / 1000, 1),
            "distance_km": round(goal_dist / 1000, 1),
            "runs": goal_runs,
            "distance_pct": round(dist_pct),
            "runs_pct": round(runs_pct),
            "distance_gap_km": round((goal_dist - totals_dist) / 1000, 1),
            "achieved": dist_pct >= 100,
            "window": [{"week_of": k.strftime("%d %b"),
                        "km": round(history_weeks[k]["distance"] / 1000, 1),
                        "runs": history_weeks[k]["runs"]} for k in window],
        },
        "runs": runs,
        "cities": cities,
        "insights": insight_lib.build(ctx),
        "weather": weather,
        "next_week": build_next_week_plan(goal_dist / 1000, goal_runs,
                                          baseline / 1000, planned,
                                          totals_dist / 1000, week_open),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Aggregate the cached running week into digest_data.json.")
    ap.add_argument("--data", help=f"cache directory (default {DATA_DIR})")
    ap.add_argument("--out", help=f"output directory (default {OUTPUT_DIR})")
    ap.add_argument("--plan", metavar="FILE",
                    help="a run plan from `plan_run.py --json` (- for stdin) to "
                         "lead the next-week section")
    ap.add_argument("--plan-km", type=float, metavar="KM",
                    help="project a planned run of this distance and lead with it")
    ap.add_argument("--start-in", type=float, default=0, metavar="HOURS",
                    help="hours until the --plan-km run starts, so its effort "
                         "band matches what planning reported")
    ap.add_argument("--json", action="store_true",
                    help="print the digest payload to stdout as well")
    args = ap.parse_args(argv)

    notes = []
    try:
        planned = load_planned_run(args.plan, args.plan_km, args.data,
                                   args.start_in)
        digest = analyze(args.data, notes, planned)
    except cache.CacheMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser() if args.out else OUTPUT_DIR
    ensure_dirs(out_dir)
    (out_dir / "digest_data.json").write_text(json.dumps(digest, indent=2) + "\n")

    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    goal, totals = digest["goal"], digest["totals"]
    print(f"Wrote {out_dir / 'digest_data.json'}")
    print(f"  {totals['distance_km']} km / {goal['distance_km']} km goal "
          f"= {goal['distance_pct']}%  ({totals['runs']}/{goal['runs']} runs)  "
          f"focus={goal['focus']}")
    if digest["week_in_progress"]:
        days = digest["days_remaining"]
        left = (f"{days} day{'s' if days != 1 else ''} left after today"
                if days else "today is the last day")
        print(f"  the week is still open ({left}), so these totals are partial")
    print(f"  {len(digest['insights'])} insights: "
          f"{', '.join(i['key'] for i in digest['insights']) or 'none fired'}")
    if digest["next_week"]["planned_run"]:
        p = digest["next_week"]["planned_run"]
        whose = "this week" if digest["week_in_progress"] else "next week"
        print(f"  plan leads with the planned {p['distance_km']:g} km "
              f"({p['recommended']['band']}), credited to {whose}")
    if args.json:
        print(json.dumps(digest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

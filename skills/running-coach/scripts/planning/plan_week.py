#!/usr/bin/env python3
"""
plan_week.py — plan the whole training week and turn it into calendar blocks.

Where plan_run.py projects a single run from a race effort, this plans the week
from the athlete's OWN recent running: it reads the weekly goal (or derives one
the way the digest does), splits it into a run mix at milestone distances,
prices each run as a pace *window* anchored on recent average and fastest pace,
assigns days and times, and emits ready-to-create Google Calendar events.

Two deliberate choices, both from how people actually train:
  * Distances are always multiples of 5 km — milestones matter.
  * Paces are windows read off recent runs, drifting slower with distance, NOT a
    race projection. A plan you can't run at the pace it prescribes is no plan.

Five stages, run in order; each prints a human summary and takes --json, and
each later stage accepts the previous stage's --json on stdin:

    python3 scripts/planning/plan_week.py goal              # what's the target?
    python3 scripts/planning/plan_week.py propose           # split + pace windows
    python3 scripts/planning/plan_week.py draft             # days, times
    python3 scripts/planning/plan_week.py brief             # facts to write against
    python3 scripts/planning/plan_week.py events --content  # calendar payloads

This file computes and nothing more. Every word that reaches the calendar is
written by the agent: `brief` hands over each run's facts and the variable
contract from calendar_event.py, and `events --content` renders what came back.
No stock pep talks live here, because a stock pep talk is the same one forever.

The scripts never call MCP and never touch a token; the agent fetches through
the connectors and this transforms the cache. Strava exposes no writable goal
field, so a goal the runner states is stored via `cache.py put goal_state`.
"""
import argparse
import json
import statistics
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/planning/
import cache
import plan_run
from calendar_event import DescriptionError, render, schema
from common.pace import MILESTONE_STEP_KM, best_projection, fmt_hms, fmt_pace
from common.paths import DATA_DIR
from common.weeks import parse_dt, week_bounds

# Each run type: the effort band it's priced at, and a relative weight used only
# to decide which runs grow first when distributing distance (not the distance
# itself — that comes in 5 km blocks).
TYPES = {
    "easy":      {"band": "easy",   "weight": 0.85, "label": "Easy run"},
    "steady":    {"band": "steady", "weight": 1.05, "label": "Steady run"},
    "long":      {"band": "easy",   "weight": 1.75, "label": "Long run"},
    "tempo":     {"band": "tempo",  "weight": 1.15, "label": "Tempo run"},
    "intervals": {"band": "hard",   "weight": 0.95, "label": "Intervals"},
}

# Short "what it's for" label, shown in the plan table's Focus column.
FOCUS = {"easy": "Recovery", "steady": "Aerobic", "long": "Endurance",
         "tempo": "Threshold", "intervals": "Speed"}


class PlanningError(Exception):
    """The week as asked for cannot be laid out — say so rather than fudge it."""

# ---- pace model: windows anchored on the athlete's own recent running --------
# Easy, steady and long are priced off the average of recent whole runs — what
# this athlete does on a normal day. All in s/km.
EASY_OVER_AVG = 8          # easy is ~8 s/km slower than the recent average
DIST_DRIFT_PER_KM = 4      # slower by this per km beyond the reference distance
DIST_DRIFT_REF_KM = 5      # runs at/under this get no drift
TEMPO_DRIFT_FACTOR = 0.5   # tempo drifts half as much (short, and it's quality)
WINDOW_S = 10              # half-width of a pace window, s/km

# Quality work needs a different anchor. A whole-run average carries no
# information about threshold or rep pace, so tempo starts from the quickest
# full run the athlete has actually completed — demonstrably sustainable — and
# reps start from a race projection, since nothing else evidences 400 m pace.
# The race projection also caps tempo: prescribing something faster than a
# projected threshold is prescribing a race, which is not what a Tuesday is for.
TEMPO_UNDER_BEST_RUN = 10  # tempo: this much faster than the quickest recent run
TEMPO_OVER_RACE = 1.06     # threshold sits ~6% slower than race pace
REP_OVER_RACE = 1.02       # reps are near enough all-out
REP_REFERENCE_M = 400      # the rep distance reps are priced at
REP_FALLBACK_GAIN = 25     # with no efforts cached, reps beat the best run by this

# Fastest to slowest. Every band must sit at least MIN_BAND_GAP_S clear of the
# one above it: with two anchors drawn from different data they can cross, and a
# tempo pace that lands slower than easy pace is an artefact, not a session.
BAND_ORDER = ("hard", "tempo", "steady", "easy")
MIN_BAND_GAP_S = 6

# How far back pace is read. Three weeks is recent enough to describe current
# fitness and long enough to average out one bad morning; the window widens
# before it gives up, so a comeback week still produces a plan.
PACE_LOOKBACK_DAYS = 21
PACE_LOOKBACK_MAX_DAYS = 56
PACE_MIN_SAMPLES = 3

DISTANCE_STEP_KM = MILESTONE_STEP_KM   # every run is a whole multiple of this

# Caps on a single run, as multiples of the athlete's longest recent run. Nobody
# should meet their longest-ever distance on a Tuesday, and the long run only
# earns a reach beyond it. Floors keep the caps usable for a beginner whose
# longest run so far is 5 km.
CAP_OF_LONGEST = {"long": 1.5, "tempo": 1.0, "steady": 1.0,
                  "intervals": 1.0, "easy": 1.0}
CAP_FLOOR_KM = {"long": 10, "tempo": 5, "steady": 5, "intervals": 5, "easy": 5}


def distance_caps(longest_km):
    """Per-type distance ceilings, in 5 km blocks, from what's been run before."""
    caps = {}
    for run_type, factor in CAP_OF_LONGEST.items():
        raw = max(CAP_FLOOR_KM[run_type], (longest_km or 0) * factor)
        caps[run_type] = int(raw // DISTANCE_STEP_KM) * DISTANCE_STEP_KM
    return caps

# Day layout: long run to Sunday, quality days spaced Tue/Thu, easy days between.
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]
LONG_WEEKDAY = 6
QUALITY_WEEKDAY = {"tempo": 1, "intervals": 3}
FILL_WEEKDAYS = [0, 2, 4, 5]

DEFAULT_START_TIME = "06:30"
DEFAULT_LOCATION = "HSR Layout, Bengaluru"
DEFAULT_TZ = "Asia/Kolkata"
POST_RUN_BUFFER_MIN = 10

# ------------------------------------------------------- recent pace stats
def effort_evidence(data_dir):
    """{distance_m: seconds} — the fastest time cached at each distance.

    Personal bests and recent per-activity best efforts, merged fastest-wins.
    Both only ever bound how fast a session may be prescribed, never set the
    target, so taking the quickest of the two cannot make a week harder than
    `best_projection` says the athlete can manage.
    """
    merged = dict(plan_run.personal_bests(data_dir))
    for metres, seconds in plan_run.recent_efforts(data_dir).items():
        if seconds < merged.get(metres, float("inf")):
            merged[metres] = seconds
    return merged


def recent_run_samples(data_dir, days=PACE_LOOKBACK_DAYS):
    """Pace and distance per run, from the last `days` only.

    A window, not the whole cache: pace read across a year of running describes
    a year-old athlete. It widens if a short window is too thin to average, so
    someone coming back from a break still gets a plan rather than an error.
    """
    runs = []
    for key in ("activities_week", "activities_history"):
        runs += cache.load(key, data_dir, required=False, default=[]) or []

    parsed = []
    for a in runs:
        s = a.get("summary", {})
        v, d = s.get("avg_speed") or 0, s.get("distance") or 0
        if v <= 0 or d <= 0:
            continue
        try:
            when = parse_dt(a["start_local"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append({"pace_sec": 1000.0 / v, "dist_km": d / 1000.0, "date": when})

    for window in (days, PACE_LOOKBACK_MAX_DAYS, None):
        if window is None:
            return sorted(parsed, key=lambda x: x["date"])
        cutoff = date.today() - timedelta(days=window)
        recent = [x for x in parsed if x["date"] >= cutoff]
        if len(recent) >= PACE_MIN_SAMPLES:
            return sorted(recent, key=lambda x: x["date"])
    return sorted(parsed, key=lambda x: x["date"])


def pace_stats(data_dir):
    """Average / fastest pace and typical distance from recent runs."""
    samples = recent_run_samples(data_dir)
    if not samples:
        raise cache.CacheMissing(
            "no recent runs cached to read pace from. Call Strava:list_activities "
            "for recent weeks, then `python3 scripts/cache.py put activities_history "
            "--file -` (or activities_week for the current week)."
        )
    paces = [x["pace_sec"] for x in samples]
    dists = [x["dist_km"] for x in samples]

    # Effort evidence that projects slower than the athlete's own quickest full
    # run is not evidence of anything: it's a years-old PB, or a "best effort"
    # clipped out of an easy jog. Discard it rather than plan around it, so the
    # fallback path runs and the printed note says which anchor was used.
    efforts = effort_evidence(data_dir)
    stale = bool(efforts) and best_projection(efforts, 5000)[1] > min(paces)
    if stale:
        efforts = {}
    return {
        "n": len(samples),
        "avg_pace_sec": statistics.mean(paces),
        "best_pace_sec": min(paces),
        "slow_pace_sec": max(paces),
        "avg_distance_km": round(statistics.mean(dists), 1),
        "median_distance_km": round(statistics.median(dists), 1),
        "longest_km": round(max(dists), 1),
        "from_date": samples[0]["date"].isoformat(),
        "to_date": samples[-1]["date"].isoformat(),
        "efforts": efforts,
        "efforts_stale": stale,
        "quality_anchor": (
            "best efforts" if efforts
            else "recent averages (cached best efforts are slower than your own "
                 "quickest run, so they were ignored)" if stale
            else "recent averages (no best efforts cached)"),
    }


def anchor_note(stats):
    """One line naming what the quality paces are priced from."""
    if stats.get("efforts_stale"):
        return ("your cached best efforts project slower than your quickest "
                "recent run, so tempo and rep paces come off recent form instead")
    if not stats.get("efforts"):
        return ("tempo is set off your quickest recent run, and rep pace is "
                "estimated — cache performance or athlete_zones to project it")
    rp = race_pace(stats, REP_REFERENCE_M / 1000)
    return (f"tempo is capped by a race projection and reps are priced at "
            f"{_mmss(rp)}/km, both from your cached best efforts")


def _mmss(sec):
    return fmt_pace(sec).replace("/km", "")


def window_str(lo, hi):
    return f"{_mmss(lo)}–{_mmss(hi)}/km"


def race_pace(stats, dist_km):
    """Projected race pace (s/km) at a distance, or None with nothing cached."""
    efforts = stats.get("efforts")
    if not efforts:
        return None
    return best_projection(efforts, dist_km * 1000)[1]


def _raw_center(band, stats, dist_km):
    """Center pace (s/km) for one band, before the ordering guard."""
    avg, best_run = stats["avg_pace_sec"], stats["best_pace_sec"]
    over = max(0, dist_km - DIST_DRIFT_REF_KM)

    if band == "hard":
        # Reps are shorter and faster than any full run, so a whole-run average
        # says nothing about them. No distance drift: the reps stay the same
        # length whatever the session totals.
        rp = race_pace(stats, REP_REFERENCE_M / 1000)
        return rp * REP_OVER_RACE if rp else best_run - REP_FALLBACK_GAIN
    if band == "tempo":
        from_form = (best_run - TEMPO_UNDER_BEST_RUN
                     + DIST_DRIFT_PER_KM * TEMPO_DRIFT_FACTOR * over)
        rp = race_pace(stats, dist_km)
        # The slower of the two: recent form is the target, the projection is a
        # ceiling, and a tempo should never out-run a projected threshold.
        return max(from_form, rp * TEMPO_OVER_RACE) if rp else from_form
    if band == "steady":
        return avg + DIST_DRIFT_PER_KM * over
    return avg + EASY_OVER_AVG + DIST_DRIFT_PER_KM * over  # easy / long


def band_centers(stats, dist_km):
    """Every band's center at one distance, forced into a defensible order.

    The guard works from the slowest band upward, so a colliding band is pulled
    *faster* and the easy end is never touched. That direction matters: easy and
    steady come from recent averages, the strongest evidence in the cache, while
    the quality bands come from projections. Slowing easy pace down to make room
    for an odd projection would corrupt the runs the athlete does most of.
    """
    centers = {b: _raw_center(b, stats, dist_km) for b in BAND_ORDER}
    slowest_first = list(reversed(BAND_ORDER))
    for slower, faster in zip(slowest_first, slowest_first[1:]):
        centers[faster] = min(centers[faster], centers[slower] - MIN_BAND_GAP_S)
    return centers


def band_center(band, stats, dist_km):
    return band_centers(stats, dist_km)[band]


def band_window(band, stats, dist_km):
    c = band_center(band, stats, dist_km)
    return c - WINDOW_S, c + WINDOW_S, c


def windows_table(stats):
    """Every band's window at each milestone distance — a quick reference."""
    rows = []
    for d in (5, 10, 15):
        row = {"distance_km": d}
        for band in BAND_ORDER:
            lo, hi, _ = band_window(band, stats, d)
            row[band] = window_str(lo, hi)
        rows.append(row)
    return rows


# ------------------------------------------------------------------- goal
def _analyze():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "digest"))
    import analyze  # noqa: E402
    return analyze


def recent_week_runs(data_dir):
    """Run counts per week, most recent first, from everything cached.

    activities_week holds the current week and activities_history the ones
    before it. Reading only history would miss the week in progress, which is
    how a plan ends up claiming a five-run habit off a two-run fortnight.
    """
    analyze = _analyze()
    activities = []
    for key in ("activities_week", "activities_history"):
        activities += cache.load(key, data_dir, required=False, default=[]) or []
    if not activities:
        return []
    weeks = analyze.aggregate_weeks(activities)
    return [(k, weeks[k]["runs"]) for k in sorted(weeks, reverse=True)]


def derived_goal(data_dir):
    analyze = _analyze()
    profile = cache.load("athlete_profile", data_dir, required=False, default={}) or {}
    focus, _ = analyze.resolve_focus(profile)
    km = plan_run.weekly_goal_km(data_dir)

    # The current week is still filling up, so it cannot speak for a habit;
    # count it only to hold the ramp back, never to justify raising it.
    weeks = recent_week_runs(data_dir)
    complete = [n for _, n in weeks[1:analyze.TRAILING_WEEKS + 1]]
    runs = int(round(statistics.median(complete))) if complete else None
    last_week = complete[0] if complete else None
    if runs and last_week is not None and last_week < runs:
        runs = last_week      # a week that fell short is not a week to build on
    return km, runs or analyze.FALLBACK_GOAL_RUNS, focus, last_week


def resolve_goal(data_dir, weekly_km=None, runs=None):
    if weekly_km is not None and weekly_km <= 0:
        raise PlanningError(f"--km must be positive, got {weekly_km:g}")
    if runs is not None and not 1 <= runs <= 7:
        raise PlanningError(f"--runs must be between 1 and 7, got {runs}")

    stored = cache.load("goal_state", data_dir, required=False, default=None)
    dk, dr, dfocus, last_week = derived_goal(data_dir)
    if stored:
        km = weekly_km or stored.get("weekly_km") or dk
        n = runs or stored.get("runs_per_week") or dr
        focus = stored.get("focus") or dfocus
        source = "goal_state"
    else:
        km, n, focus, source = weekly_km or dk, runs or dr, dfocus, "derived"
    if not km:
        raise cache.CacheMissing(
            "no weekly distance to plan against: there's no stored goal and no "
            "history to derive one from. Set a goal — `python3 scripts/cache.py "
            "put goal_state --set weekly_km=<km> --set runs_per_week=<n> "
            "--set focus=build` — or cache activities_history."
        )
    # Milestones: the weekly target is a multiple of the distance step too.
    asked_km = float(km)
    km = round(round(asked_km / DISTANCE_STEP_KM) * DISTANCE_STEP_KM, 1)
    return {
        "weekly_km": km,
        "asked_weekly_km": round(asked_km, 1),
        "runs_per_week": int(n),
        "focus": focus,
        "source": source,
        "has_stored_goal": bool(stored),
        "last_complete_week_runs": last_week,
        "stored": stored,
    }


# ---------------------------------------------------------------- the split
def split_template(n, quality=True):
    n = max(1, min(n, 7))
    if n == 1:
        return ["long"]
    if n == 2:
        return ["steady", "long"]
    if n == 3:
        return ["easy", "tempo" if quality else "steady", "long"]
    if n == 4:
        return ["easy", "tempo", "steady", "long"]
    if n == 5:
        return ["easy", "tempo", "easy", "steady", "long"]
    if n == 6:
        return ["easy", "tempo", "easy", "intervals", "steady", "long"]
    return ["easy", "tempo", "easy", "intervals", "steady", "easy", "long"]


def distances_m5(types, weekly_km, caps):
    """Split the week into per-run distances, every one a multiple of 5 km.

    Everyone starts at one 5 km block (the milestone floor); the remaining
    blocks go to the runs that should carry the most volume first (long, then
    the quality day, then steady), each capped so no single run runs away.

    The long run takes its second block before anything else gets one. A "long
    run" the same length as Monday's recovery jog is a label, not a session, and
    at low weekly volume that is exactly where the round-robin would leave it.
    """
    n = len(types)
    step = DISTANCE_STEP_KM
    target = max(step * n, round(weekly_km / step) * step)
    dist = {i: step for i in range(n)}
    units = (target - step * n) // step
    priority = sorted(range(n), key=lambda i: -TYPES[types[i]]["weight"])

    long_idx = next((i for i, t in enumerate(types) if t == "long"), None)
    if (units > 0 and long_idx is not None and n > 1
            and dist[long_idx] + step <= caps["long"]):
        dist[long_idx] += step
        units -= 1

    i = 0
    while units > 0:
        if all(dist[j] >= caps[types[j]] for j in range(n)):
            break                      # everyone capped — can't place more
        idx = priority[i % n]
        if dist[idx] + step <= caps[types[idx]]:
            dist[idx] += step
            units -= 1
        i += 1
    return [dist[i] for i in range(n)]


def session_shape(run_type, dist_km):
    """How the distance breaks up, in numbers — no prose.

    A tempo or interval session is not run at one pace throughout, so the plan
    owns the arithmetic of how it splits. Turning these numbers into a sentence
    is the agent's job, which is why nothing here returns a phrase. Continuous
    runs get an empty shape: there is nothing to divide.
    """
    if run_type == "tempo":
        warm = max(1, round(dist_km * 0.2))
        return {"warmup_km": warm, "working_km": dist_km - 2 * warm,
                "cooldown_km": warm}
    if run_type == "intervals":
        # The reps and the jog recoveries between them share what is left after
        # a km each way, because the recoveries are distance the athlete covers
        # and Strava records: (2n - 1) rep-lengths for n reps. Floored, so the
        # session lands at or under the distance the week budgeted for it.
        rep_m = 400 if dist_km <= 5 else (800 if dist_km <= 8 else 1000)
        work_m = max(rep_m, (dist_km - 2) * 1000)
        reps = max(3, int((work_m / rep_m + 1) // 2))
        return {"warmup_km": 1, "reps": reps, "rep_m": rep_m,
                "recovery_m": rep_m, "cooldown_km": 1}
    return {}


def session_duration(run_type, dist_km, shape, pace_at):
    """Seconds the whole session takes; `pace_at(band)` supplies each s/km.

    Priced piece by piece, because most of a quality session's distance is not
    run at the pace it is named for. Charging an interval day at rep pace for
    all 5 km books a calendar block that ends while the runner is still out.
    """
    if run_type == "tempo":
        easy_km = shape["warmup_km"] + shape["cooldown_km"]
        return easy_km * pace_at("easy") + shape["working_km"] * pace_at("tempo")
    if run_type == "intervals":
        rep_km = shape["rep_m"] / 1000.0
        easy_km = (shape["warmup_km"] + shape["cooldown_km"]
                   + (shape["reps"] - 1) * shape["recovery_m"] / 1000.0)
        return easy_km * pace_at("easy") + shape["reps"] * rep_km * pace_at("hard")
    return dist_km * pace_at(TYPES[run_type]["band"])


def shape_line(run_type, shape, window):
    """The shape as one factual line, for the CLI tables only."""
    if run_type == "tempo":
        return (f"{shape['warmup_km']:g} km warm-up + {shape['working_km']:g} km "
                f"at {window} + {shape['cooldown_km']:g} km cool-down")
    if run_type == "intervals":
        return (f"{shape['warmup_km']:g} km warm-up + {shape['reps']} × "
                f"{shape['rep_m']} m at {window} with {shape['recovery_m']} m "
                f"jog recoveries + {shape['cooldown_km']:g} km cool-down")
    return "continuous"


def reconcile(goal, dists, types, caps, stats):
    """Every way the plan differs from what was asked, in the runner's words.

    Three rules quietly move the numbers: distances round to 5 km blocks, no
    single run may exceed what the athlete has actually run, and every run gets
    at least one block. Left unsaid, they look like arithmetic errors — a 20 km
    goal coming back as a 25 km plan reads as a bug, not a milestone rule.
    """
    notes = []
    planned = sum(dists)
    asked, target = goal["asked_weekly_km"], goal["weekly_km"]

    if abs(asked - target) >= 0.1:
        notes.append(
            f"your {asked:g} km goal was rounded to {target:g} km so every run "
            f"lands on a {DISTANCE_STEP_KM} km milestone")
    if planned != target:
        floor_km = DISTANCE_STEP_KM * len(types)
        if planned > target and target < floor_km:
            notes.append(
                f"{len(types)} runs cannot total less than {floor_km:g} km when "
                f"the shortest run is {DISTANCE_STEP_KM} km — the plan is "
                f"{planned:g} km. Drop to {int(target // DISTANCE_STEP_KM)} runs "
                f"to hit {target:g} km exactly")
        elif planned < target:
            notes.append(
                f"the plan totals {planned:g} km, {target - planned:g} km under "
                f"your {target:g} km goal: every run is at its cap for someone "
                f"whose longest is {stats['longest_km']:g} km. Add a run, or "
                f"extend your long run first")
        else:
            notes.append(f"the plan totals {planned:g} km against a "
                         f"{target:g} km goal")

    long_km = next((d for d, t in zip(dists, types) if t == "long"), None)
    if long_km and stats["longest_km"] and long_km > stats["longest_km"]:
        notes.append(
            f"the {long_km:g} km long run is further than you've run in the "
            f"cached weeks ({stats['longest_km']:g} km) — a real step up, so "
            f"treat the pace window as a ceiling")
    if long_km and len(types) > 1 and long_km <= max(
            d for d, t in zip(dists, types) if t != "long"):
        notes.append(
            f"the long run is only {long_km:g} km, no further than another run "
            f"this week — at this volume it is the week's anchor in name only")

    last = goal.get("last_complete_week_runs")
    if last is not None and last < goal["runs_per_week"]:
        notes.append(
            f"you ran {last} time(s) in the last complete week against "
            f"{goal['runs_per_week']} planned — the count came from your median "
            f"week, so this is a step up, not your current habit")
    return {"planned_total_km": planned, "goal_km": target,
            "caps_km": caps, "notes": notes}


def build_split(data_dir, weekly_km=None, runs=None):
    goal = resolve_goal(data_dir, weekly_km, runs)
    stats = pace_stats(data_dir)
    quality = goal["focus"] in ("build", "peak")
    types = split_template(goal["runs_per_week"], quality)
    caps = distance_caps(stats["longest_km"])
    dists = distances_m5(types, goal["weekly_km"], caps)

    runs_out = []
    for t, dist in zip(types, dists):
        band = TYPES[t]["band"]
        centers = band_centers(stats, dist)
        center = centers[band]
        win = window_str(center - WINDOW_S, center + WINDOW_S)
        shape = session_shape(t, dist)
        # The session's own duration, not the headline pace times the distance:
        # a warm-up and jog recoveries are run at easy pace and take real time.
        mid = session_duration(t, dist, shape, lambda b: centers[b])
        fast = session_duration(t, dist, shape, lambda b: centers[b] - WINDOW_S)
        slow = session_duration(t, dist, shape, lambda b: centers[b] + WINDOW_S)
        runs_out.append({
            "type": t,
            "label": TYPES[t]["label"],
            "distance_km": dist,
            "band": band,
            "pace": win,                       # the pace window (faster–slower)
            "pace_center_sec": round(center),
            "finish": fmt_hms(mid),            # midpoint estimate
            "finish_sec": round(mid),
            "finish_range": f"{fmt_hms(fast)}–{fmt_hms(slow)}",
            "shape": shape,
            "focus": FOCUS[t],
        })
    return {
        "goal": goal,
        "pace_stats": {
            "runs_analysed": stats["n"],
            "avg_pace": f"{_mmss(stats['avg_pace_sec'])}/km",
            "fastest_pace": f"{_mmss(stats['best_pace_sec'])}/km",
            "avg_distance_km": stats["avg_distance_km"],
            "longest_km": stats["longest_km"],
            "from_date": stats["from_date"],
            "to_date": stats["to_date"],
            "easy_anchor": "average of recent whole runs",
            "quality_anchor": stats["quality_anchor"],
            "anchor_note": anchor_note(stats),
        },
        "pace_windows_by_distance": windows_table(stats),
        "planned_total_km": sum(dists),
        "reconciliation": reconcile(goal, dists, types, caps, stats),
        "runs": runs_out,
    }


# ------------------------------------------------------------------- the draft
# A hard day is one the legs need to recover from, so two must not sit next to
# each other. The long run counts: it is the week's biggest single load.
HARD_TYPES = ("long", "tempo", "intervals")


def assign_weekdays(types, usable=None):
    """Map each run to a weekday.

    Days in `usable` (those still ahead when planning mid-week) come first, and
    hard days avoid landing next to one another — a guarantee that has to be
    re-checked after any mid-week shuffle, not just assumed from the template.
    """
    usable = set(range(7)) if usable is None else set(usable)
    slots, used, hard_days = {}, set(), set()

    def take(idx, wd, hard):
        slots[idx] = wd
        used.add(wd)
        if hard:
            hard_days.add(wd)

    def place(idx, preferred, hard=False):
        order = [preferred] + FILL_WEEKDAYS + list(range(7))
        if hard:                     # a usable day with no hard neighbour
            for wd in order:
                if (wd not in used and wd in usable
                        and not hard_days & {wd - 1, wd + 1}):
                    return take(idx, wd, hard)
        for wd in order:                        # any usable, free day
            if wd not in used and wd in usable:
                return take(idx, wd, hard)
        for wd in order:                        # else any free day (flagged past)
            if wd not in used:
                return take(idx, wd, hard)

    for i, t in enumerate(types):
        if t == "long":
            place(i, LONG_WEEKDAY, hard=True)
    for i, t in enumerate(types):
        if t in QUALITY_WEEKDAY:
            place(i, QUALITY_WEEKDAY[t], hard=True)
    for i, t in enumerate(types):
        if i not in slots:
            place(i, FILL_WEEKDAYS[0])
    return slots


def adjacent_hard_days(sessions):
    """Pairs of back-to-back hard sessions, so the draft can own up to them."""
    hard = {date.fromisoformat(s["date"]): s for s in sessions
            if s["type"] in HARD_TYPES}
    return [f"{a.isoformat()} {hard[a]['label']} then "
            f"{(a + timedelta(days=1)).isoformat()} {hard[a + timedelta(days=1)]['label']}"
            for a in sorted(hard) if a + timedelta(days=1) in hard]


def build_draft(split, start_date=None, start_time=DEFAULT_START_TIME,
                location=DEFAULT_LOCATION, weekend_time=None, skip_past=True,
                fill_week=True, as_of=None):
    now = as_of or datetime.now()
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    monday, sunday = week_bounds(start_date or now.date())
    weekday_t = time.fromisoformat(start_time)
    weekend_t = time.fromisoformat(weekend_time) if weekend_time else weekday_t

    def start_for(wd):
        t0 = weekend_t if wd >= 5 else weekday_t
        return datetime.combine(monday + timedelta(days=wd), t0)

    # Preserve the weekly total when planning mid-week: place runs only on days
    # whose start is still ahead, so a slot already gone (this morning, say) is
    # skipped in favour of a free later day rather than dropping the run.
    usable = ({wd for wd in range(7) if start_for(wd) > now}
              if fill_week else set(range(7)))

    # More runs than days left cannot be honoured, and quietly parking the
    # overflow on days already gone would report a week the calendar never
    # receives. Drop the lowest-priority runs instead, and say which.
    runs, dropped = list(split["runs"]), []
    if fill_week and len(runs) > len(usable):
        if not usable:
            raise PlanningError(
                f"no day left in the week of {monday} has a "
                f"{start_time} start still ahead. Plan next week with "
                f"--start-date {(monday + timedelta(days=7)).isoformat()}, or "
                f"pass --time for a later start today."
            )
        keep = sorted(sorted(range(len(runs)),
                            key=lambda i: -TYPES[runs[i]["type"]]["weight"]
                            )[:len(usable)])
        dropped = [runs[i] for i in range(len(runs)) if i not in set(keep)]
        runs = [runs[i] for i in keep]

    types = [r["type"] for r in runs]
    slots = assign_weekdays(types, usable)

    sessions = []
    for i, run in enumerate(runs):
        wd = slots[i]
        run_date = monday + timedelta(days=wd)
        start_dt = start_for(wd)
        end_dt = start_dt + timedelta(seconds=run["finish_sec"] + POST_RUN_BUFFER_MIN * 60)
        bump = (5 - end_dt.minute % 5) % 5
        end_dt = (end_dt + timedelta(minutes=bump)).replace(second=0, microsecond=0)
        sessions.append({
            **run,
            "weekday": WEEKDAY_NAMES[wd],
            "date": run_date.isoformat(),
            "start": start_dt.isoformat(timespec="seconds"),
            "end": end_dt.isoformat(timespec="seconds"),
            "location": location,
            "past": skip_past and start_dt <= now,
        })
    sessions.sort(key=lambda s: s["start"])
    # Progress counts only the runs that will actually be created, so a block
    # never claims to be "run 2 of 5" in a week the calendar sees three of.
    scheduled = [s for s in sessions if not s["past"]]
    total_km = sum(x["distance_km"] for x in scheduled)
    cum = 0.0
    for idx, x in enumerate(scheduled, 1):
        cum += x["distance_km"]
        x["progress"] = {"index": idx, "total_runs": len(scheduled),
                         "cum_km": round(cum, 1), "total_km": round(total_km, 1)}
    rest = [WEEKDAY_NAMES[wd] for wd in range(7) if wd not in slots.values()]
    return {
        "goal": split["goal"],
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "location": location,
        "start_time": start_time,
        "weekend_time": weekend_time or start_time,
        "planned_total_km": split["planned_total_km"],
        "scheduled_total_km": round(total_km, 1),
        "scheduled_runs": len(scheduled),
        "dropped": [{"label": r["label"], "type": r["type"],
                     "distance_km": r["distance_km"],
                     "reason": "no day left in the week for it"}
                    for r in dropped],
        "adjacent_hard_days": adjacent_hard_days(scheduled),
        "rest_days": rest,
        "sessions": sessions,
    }


# ------------------------------------------------------------------- brief
def event_summary(session):
    return f"{session['label']} · {session['distance_km']:g} km ({session['band']})"


def scheduled_sessions(draft, include_past=False):
    return [s for s in draft["sessions"]
            if include_past or not s.get("past")]


def build_brief(draft, include_past=False, data_dir=None):
    """The facts of each run, plus the contract its description must satisfy.

    This is the handover: everything computable about the week is here, and the
    words are not. The agent writes one content object per run against
    `calendar_event.py`'s variables, then `events --content` renders them.
    """
    goal = draft["goal"]
    forecast = cache.load("weather_forecast", data_dir, required=False, default={}) or {}
    by_date = forecast.get("by_date", {}) if isinstance(forecast, dict) else {}
    runs = []
    for s in scheduled_sessions(draft, include_past):
        runs.append({
            "date": s["date"],
            "weekday": s["weekday"],
            "start_time": s["start"][11:16],
            "summary": event_summary(s),
            "type": s["type"],
            "label": s["label"],
            "band": s["band"],
            "focus": s["focus"],
            "distance_km": s["distance_km"],
            "pace_window": s["pace"],
            "estimated_time": s["finish_range"],
            "shape": s["shape"],
            "location": s["location"],
            "progress": s.get("progress") or {},
            "forecast": by_date.get(s["date"]) or {},
        })
    return {
        "week_start": draft["week_start"],
        "week_end": draft["week_end"],
        "goal": {"weekly_km": goal["weekly_km"],
                 "runs_per_week": goal["runs_per_week"],
                 "focus": goal["focus"]},
        "rest_days": draft["rest_days"],
        "runs": runs,
        "write": {
            "instruction":
                "Write one content object per run, keyed by the run's date, "
                "then render with `plan_week.py events --content <file>`. Use "
                "each run's facts above verbatim for the numbers; the prose is "
                "yours. Vary it across the week — seven blocks that read the "
                "same are worse than none.",
            "contract": schema(as_json=True),
        },
    }


# ------------------------------------------------------------------- events
def build_events(draft, content, tz=DEFAULT_TZ, include_past=False):
    """Calendar payloads, with descriptions rendered from written content."""
    if not isinstance(content, dict) or not content:
        raise DescriptionError(
            "no descriptions to render. Run `plan_week.py brief` for each run's "
            "facts and the content contract, write one object per run keyed by "
            "its date, then pass it back with `events --content <file>`."
        )
    sessions = scheduled_sessions(draft, include_past)
    missing = [s["date"] for s in sessions if s["date"] not in content]
    if missing:
        raise DescriptionError(
            f"no content written for {', '.join(missing)}. Every scheduled run "
            f"needs its own object, keyed by date — see `plan_week.py brief`."
        )
    events = []
    for s in sessions:
        try:
            description = render(content[s["date"]])
        except DescriptionError as exc:
            raise DescriptionError(f"{s['date']} ({s['label']}): {exc}") from None
        events.append({
            "summary": event_summary(s),
            "startTime": s["start"],
            "endTime": s["end"],
            "timeZone": tz,
            "location": s["location"],
            "description": description,
            "overrideReminders": [{"method": "popup", "minutes": 30}],
        })
    return {
        "calendar_timezone": tz,
        "week_start": draft["week_start"],
        "week_end": draft["week_end"],
        "event_count": len(events),
        # The next run's distance, so the playlist and digest can be sized to it
        # without re-deriving the week — the same handoff plan_run offers.
        "next_run_km": sessions[0]["distance_km"] if sessions else None,
        "events": events,
    }


# ------------------------------------------------------------------- stdin
def read_json_stdin(flag_value):
    if flag_value is None:
        return None
    if flag_value == "-":
        if sys.stdin.isatty():
            raise SystemExit("nothing on stdin to read the previous stage from")
        return json.loads(sys.stdin.read())
    return json.loads(Path(flag_value).expanduser().read_text())


# ------------------------------------------------------------------- printing
def print_goal(g):
    print(f"Weekly goal      : {g['weekly_km']:g} km across {g['runs_per_week']} "
          f"runs  (focus: {g['focus']})")
    if g["has_stored_goal"]:
        st = g["stored"]
        if st.get("type") == "race" and st.get("race_name"):
            print(f"Source           : stored goal — race «{st['race_name']}»"
                  + (f" on {st['race_date']}" if st.get("race_date") else ""))
        else:
            print("Source           : stored goal (goal_state)")
    else:
        print("Source           : derived from your trailing mileage — Strava has "
              "no weekly-goal field")
        print(f"                   python3 scripts/cache.py put goal_state "
              f"--set weekly_km={g['weekly_km']:g} "
              f"--set runs_per_week={g['runs_per_week']} --set focus={g['focus']}")


def _row(cells, widths):
    return "  " + "".join(f"{c:<{w}}" for c, w in zip(cells, widths))


def print_split(split):
    g, ps = split["goal"], split["pace_stats"]
    print(f"Recent form: avg {ps['avg_pace']} · fastest {ps['fastest_pace']} · "
          f"avg {ps['avg_distance_km']} km/run · longest {ps['longest_km']} km")
    print(f"             from {ps['runs_analysed']} runs, "
          f"{ps['from_date']} to {ps['to_date']}")
    print(f"Easy, steady and long paces come off that average; "
          f"{ps['anchor_note']}.\n")

    print("Pace windows by distance (per km)")
    w = [10, 14, 14, 14, 13]
    print(_row(["Distance", "Easy", "Steady", "Tempo", "Reps"], w))
    print(_row(["-" * 8, "-" * 12, "-" * 12, "-" * 12, "-" * 11], w))
    for r in split["pace_windows_by_distance"]:
        print(_row([f"{r['distance_km']} km", r["easy"], r["steady"], r["tempo"],
                    r["hard"]], w))

    n = len(split["runs"])
    print(f"\nGoal: {g['weekly_km']:g} km / {g['runs_per_week']} "
          f"run{'' if g['runs_per_week'] == 1 else 's'} "
          f"({g['source']}, focus {g['focus']})")
    print(f"Proposed split — {split['planned_total_km']:g} km, {n} "
          f"run{'' if n == 1 else 's'} (every distance a multiple of "
          f"{DISTANCE_STEP_KM} km)\n")
    w = [12, 7, 15, 17, 11]
    print(_row(["Run", "Dist", "Pace window", "Est. time", "Focus"], w))
    print(_row(["-" * 10, "-" * 5, "-" * 13, "-" * 15, "-" * 9], w))
    for r in split["runs"]:
        print(_row([r["label"], f"{r['distance_km']:g} km", r["pace"],
                    r["finish_range"], r["focus"]], w))
    shaped = [r for r in split["runs"] if r["shape"]]
    if shaped:
        print("\n  Structure:")
        for r in shaped:
            print(f"    {r['label']}: "
                  f"{shape_line(r['type'], r['shape'], r['pace'])}")
    for note in split["reconciliation"]["notes"]:
        print(f"\n  Note: {note}.")
    print("\nConfirm the number of runs and the mix, then: draft")


def print_draft(draft):
    wk = draft["start_time"]
    we = draft.get("weekend_time", wk)
    when = wk if we == wk else f"weekdays {wk}, weekends {we}"
    n = draft["scheduled_runs"]
    print(f"Week: {draft['week_start']} to {draft['week_end']} · "
          f"{draft['scheduled_total_km']:g} km / {n} run{'' if n == 1 else 's'} "
          f"· {when} · {draft['location']}")
    planned = draft["planned_total_km"]
    if planned != draft["scheduled_total_km"]:
        print(f"      {planned:g} km was proposed; {planned - draft['scheduled_total_km']:g} "
              f"km of it falls on days already gone and will not be created.")
    print()
    w = [10, 12, 7, 12, 7, 15, 17, 11]
    print(_row(["Day", "Date", "Time", "Run", "Dist", "Pace window",
                "Est. time", "Focus"], w))
    print(_row(["-" * 9, "-" * 11, "-" * 6, "-" * 10, "-" * 5, "-" * 13,
                "-" * 15, "-" * 9], w))
    for s in draft["sessions"]:
        tag = "  (past)" if s.get("past") else ""
        print(_row([s["weekday"], s["date"], s["start"][11:16], s["label"],
                    f"{s['distance_km']:g} km", s["pace"], s["finish_range"],
                    s["focus"] + tag], w))
    if draft["rest_days"]:
        print(f"\n  Rest: {', '.join(draft['rest_days'])}")
    if draft["dropped"]:
        what = ", ".join(f"{d['label']} {d['distance_km']:g} km"
                         for d in draft["dropped"])
        print(f"\n  Dropped: {what} — no day left in the week for them")
    for pair in draft["adjacent_hard_days"]:
        print(f"\n  Back-to-back hard days: {pair}. Too few free days to "
              f"separate them — drop one to an easy effort if the legs object.")
    print("\nConfirm the days and times, then: brief")


def print_brief(brief):
    g = brief["goal"]
    print(f"Week: {brief['week_start']} to {brief['week_end']} · "
          f"{g['weekly_km']:g} km / {g['runs_per_week']} runs · focus {g['focus']}")
    print(f"{len(brief['runs'])} run(s) need a description written.\n")
    for r in brief["runs"]:
        print(f"  {r['date']}  {r['weekday']:<9} {r['start_time']}  {r['summary']}")
        print(f"      {r['distance_km']:g} km · {r['pace_window']} · "
              f"est {r['estimated_time']} · {r['focus'].lower()}")
        if r["shape"]:
            print(f"      shape: {json.dumps(r['shape'])}")
        if r["progress"]:
            p = r["progress"]
            print(f"      run {p['index']} of {p['total_runs']} · "
                  f"{p['cum_km']:g} of {p['total_km']:g} km")
        if r["forecast"]:
            print(f"      forecast: {json.dumps(r['forecast'])}")
    print("\nWrite one content object per run, keyed by date, against the "
          "contract:\n  python3 scripts/planning/calendar_event.py --schema")
    print("Then render:\n  python3 scripts/planning/plan_week.py events "
          "--content <file>")
    print("\nRerun with --json for the facts and the contract as JSON.")


def print_events(payload):
    print(f"{payload['event_count']} calendar block(s), "
          f"{payload['week_start']} to {payload['week_end']}  "
          f"(timezone {payload['calendar_timezone']}):\n")
    for e in payload["events"]:
        print(f"  {e['startTime'][:10]}  {e['startTime'][11:16]}–{e['endTime'][11:16]}"
              f"  {e['summary']}")
        print(f"      @ {e['location']}")
    print("\nThese are the payloads for Google_Calendar:create_event (one call "
          "each). Nothing is created until you make those calls.")
    nxt = next((e for e in payload["events"]), None)
    if nxt:
        km = payload.get("next_run_km")
        print(f"\nNext: playlist  → python3 scripts/playlist/build_playlist.py {km:g}"
              f"   (sized to {nxt['startTime'][:10]}'s run)")
        print(f"      digest     → python3 scripts/digest/run_pipeline.py "
              f"--plan-km {km:g}")


# ------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Plan the training week and turn it into calendar blocks.")
    ap.add_argument("--data", help=f"cache directory (default {DATA_DIR})")
    sub = ap.add_subparsers(dest="stage", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=argparse.SUPPRESS)
    common.add_argument("--km", type=float, help="override the weekly distance")
    common.add_argument("--runs", type=int, help="override the number of runs")
    common.add_argument("--json", action="store_true")

    # Scheduling flags, shared by every stage that has to lay runs out on days.
    sched = argparse.ArgumentParser(add_help=False)
    sched.add_argument("--split", help="propose --json on stdin ('-') or a file")
    sched.add_argument("--start-date",
                       help="any date in the target week (default: today)")
    sched.add_argument("--time", default=DEFAULT_START_TIME, dest="start_time",
                       help=f"weekday start time HH:MM (default {DEFAULT_START_TIME})")
    sched.add_argument("--weekend-time", dest="weekend_time",
                       help="start time HH:MM for Sat/Sun (default: same as --time)")
    sched.add_argument("--location", default=DEFAULT_LOCATION,
                       help=f"starting point (default {DEFAULT_LOCATION!r})")
    sched.add_argument("--no-fill-week", dest="fill_week", action="store_false",
                       help="drop runs whose slot is already past instead of "
                            "moving them to a free day (default: keep the total)")
    sched.set_defaults(fill_week=True)
    sched.add_argument("--as-of", help=argparse.SUPPRESS)

    sub.add_parser("goal", parents=[common], help="show the weekly goal in force")
    sub.add_parser("propose", parents=[common], help="propose the split of runs")
    sub.add_parser("draft", parents=[common, sched],
                   help="assign the split to days, times and paces")

    b = sub.add_parser("brief", parents=[common, sched],
                       help="each run's facts plus the contract its "
                            "description must satisfy — the agent writes the words")
    b.add_argument("--draft", help="draft --json on stdin ('-') or a file")
    b.add_argument("--include-past", action="store_true",
                   help="also brief days already past in the week")

    e = sub.add_parser("events", parents=[common, sched],
                       help="render written content into Calendar payloads")
    e.add_argument("--draft", help="draft --json on stdin ('-') or a file")
    e.add_argument("--content", metavar="FILE|-", required=True,
                   help="the descriptions you wrote, one object per run keyed "
                        "by date (see the brief stage)")
    e.add_argument("--tz", default=DEFAULT_TZ, help=f"IANA tz (default {DEFAULT_TZ})")
    e.add_argument("--include-past", action="store_true",
                   help="also emit events for days already past in the week")

    args = ap.parse_args(argv)
    data = getattr(args, "data", None)

    def draft_for():
        """The draft a later stage works from: given, or rebuilt from scratch."""
        given = read_json_stdin(getattr(args, "draft", None))
        if given is not None:
            return given
        split = read_json_stdin(args.split) or build_split(data, args.km, args.runs)
        return build_draft(split, args.start_date, args.start_time,
                           args.location, args.weekend_time,
                           fill_week=args.fill_week,
                           as_of=(datetime.fromisoformat(args.as_of)
                                  if args.as_of else None))

    try:
        if args.stage == "goal":
            g = resolve_goal(data, args.km, args.runs)
            print(json.dumps(g, indent=2)) if args.json else print_goal(g)
            return 0
        if args.stage == "propose":
            split = build_split(data, args.km, args.runs)
            print(json.dumps(split, indent=2)) if args.json else print_split(split)
            return 0
        if args.stage == "draft":
            draft = draft_for()
            print(json.dumps(draft, indent=2)) if args.json else print_draft(draft)
            return 0
        if args.stage == "brief":
            brief = build_brief(draft_for(), args.include_past, data)
            if args.json:
                print(json.dumps(brief, indent=2, ensure_ascii=False))
            else:
                print_brief(brief)
            return 0
        if args.stage == "events":
            content = read_json_stdin(args.content)
            payload = build_events(draft_for(), content, args.tz, args.include_past)
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print_events(payload)
            return 0
    except (cache.CacheMissing, DescriptionError, PlanningError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

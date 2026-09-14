"""Between-the-lines insights for the weekly digest.

Each generator is a pure function of the week's data that returns
{"key", "title", "text"} when its pattern is present and None when it isn't,
so a quiet week simply yields fewer insights instead of inventing them. The
digest renders whatever comes back, in the order declared by GENERATORS.
"""
import statistics
from dataclasses import dataclass, field

from common.pace import fmt_hms, fmt_pace, speed_to_pace

# --- thresholds, all expressed in the units of the thing they measure --------

# 5 seconds across a kilometre is under 1% variation — tighter than the drift a
# phone GPS introduces between two runs, so it reads as deliberate pacing.
EVEN_PACING_SPREAD_SEC = 5

# 1.6 m/s is about 10:25/km: slower than a brisk walk. Dropping below it means
# the runner has effectively stopped, not merely slowed.
NEAR_STOP_MPS = 1.6
MIN_NEAR_STOPS = 3          # one dip is a traffic light; several is a story

# Within 3% of a personal best counts as racing it; beyond 12% is aerobic-easy
# territory. The band between the two is unremarkable, so no insight fires.
PR_MATCH_PCT = 3.0
PR_EASY_PCT = 12.0

# Two runs whose distances land within 500 m of each other on the same GPS
# start are the same loop, allowing for where the watch was stopped.
LOOP_TOLERANCE_KM = 0.5
MIN_LOOP_RUNS = 2


@dataclass
class DigestContext:
    """Everything the generators are allowed to look at."""
    week: list                        # normalised activity records, this week
    history: list                     # normalised activity records, prior weeks
    performance: dict                 # activity id -> best efforts
    streams: dict                     # activity id -> sample series
    runs: list                        # the digest's per-run detail rows
    history_weeks: dict              # week-start date -> weekly totals
    goal_runs: int
    personal_bests: dict = field(default_factory=dict)  # label -> seconds

    def best_efforts(self, label):
        """Every recorded effort at `label` this week, as (activity_id, seconds)."""
        out = []
        for a in self.week:
            for e in self.performance.get(a["id"], {}).get("best_efforts", []):
                if e["display_text"] == label:
                    out.append((a["id"], e["value"]))
        return out

    def run_label(self, activity_id):
        for r in self.runs:
            if r["id"] == activity_id:
                return r["label"] or r["name"] or r["date"]
        return "that run"


def even_pacing(ctx):
    efforts = ctx.best_efforts("1K")
    if len(efforts) < 2:
        return None
    times = [t for _, t in efforts]
    spread = max(times) - min(times)
    if spread > EVEN_PACING_SPREAD_SEC:
        return None
    return {
        "key": "even_pacing",
        "title": "Your legs have a metronome",
        "text": (f"Across {len(times)} runs this week your fastest kilometre landed within "
                 f"{spread}s of itself every time ({fmt_pace(min(times))} to "
                 f"{fmt_pace(max(times))}). Different days, different weather, "
                 "same engine setting."),
    }


def effort_vs_pr(ctx):
    pr = ctx.personal_bests.get("5K")
    efforts = ctx.best_efforts("5K")
    if not pr or not efforts:
        return None
    best = min(t for _, t in efforts)
    gap = (best - pr) / pr * 100
    if gap <= PR_MATCH_PCT:
        return {
            "key": "effort_vs_pr",
            "title": "You went looking for the gears",
            "text": (f"The week's quickest 5k ({fmt_hms(best)}) came within "
                     f"{abs(gap):.0f}% of your {fmt_hms(pr)} best. That wasn't a "
                     "training run, that was a question asked of your fitness."),
        }
    if gap >= PR_EASY_PCT:
        return {
            "key": "effort_vs_pr",
            "title": "Firmly in chill mode",
            "text": (f"Your fastest 5k this week ({fmt_hms(best)}) sits {gap:.0f}% off "
                     f"your {fmt_hms(pr)} best. Translation: pure easy aerobic base, "
                     "gears untouched. Nothing wrong with that in a build block."),
        }
    return None


def pace_collapse(ctx):
    """Find the run with the most near-stops; the GPS timestamps the bad weather."""
    worst = None
    for aid, series in ctx.streams.items():
        velocity = series.get("velocity_smooth") or []
        distance = series.get("distance") or []
        if len(velocity) < 2 or len(distance) != len(velocity):
            continue
        dips = sum(1 for v in velocity[1:] if v < NEAR_STOP_MPS)
        if dips < MIN_NEAR_STOPS:
            continue
        slowest = min(range(1, len(velocity)), key=lambda i: velocity[i])
        candidate = {
            "id": aid, "dips": dips,
            "speed": velocity[slowest], "km": distance[slowest] / 1000,
        }
        if worst is None or dips > worst["dips"]:
            worst = candidate
    if not worst:
        return None
    return {
        "key": "pace_collapse",
        "title": f"The {worst['km']:.1f} km wall",
        "text": (f"On {ctx.run_label(worst['id'])} your pace cratered to a near-walk "
                 f"(~{worst['speed']:.1f} m/s, {fmt_pace(speed_to_pace(worst['speed']))}) "
                 f"around the {worst['km']:.1f} km mark — one of {worst['dips']} separate "
                 "near-stops on that run. The GPS time-stamped every one of them."),
    }


def consistency_streak(ctx):
    """How long the athlete had been hitting the run count before this week."""
    this_week = len(ctx.week)
    if this_week >= ctx.goal_runs:
        return None
    completed = sorted(ctx.history_weeks, reverse=True)
    streak = 0
    for wk in completed:
        if ctx.history_weeks[wk]["runs"] >= ctx.goal_runs:
            streak += 1
        else:
            break
    if streak < 2:
        return None
    return {
        "key": "consistency_streak",
        "title": "The week the routine blinked",
        "text": (f"After {streak} straight weeks of {ctx.goal_runs}+ runs, this week "
                 f"came to {this_week}. The streak didn't break your fitness — but the "
                 f"calendar noticed. {this_week} data points instead of {ctx.goal_runs} "
                 "is the whole story."),
    }


def repeated_loop(ctx):
    """Same place, same distance, week after week: a circuit, not an exploration."""
    if len(ctx.week) < MIN_LOOP_RUNS:
        return None
    places = {(a.get("location_summary") or "").strip() for a in ctx.week}
    distances = [a["summary"]["distance"] / 1000 for a in ctx.week]
    spread = max(distances) - min(distances)
    if len(places) != 1 or spread > LOOP_TOLERANCE_KM:
        return None
    place = next(iter(places)) or "the same start pin"
    return {
        "key": "repeated_loop",
        "title": "Creature of habit",
        "text": (f"Every run this week started in {place} and finished within "
                 f"{spread * 1000:.0f} m of the same distance "
                 f"(~{statistics.mean(distances):.1f} km). You're not exploring, "
                 "you're grinding a known circuit — which is exactly why your splits "
                 "are so repeatable."),
    }


GENERATORS = (even_pacing, effort_vs_pr, pace_collapse, consistency_streak, repeated_loop)


def build(ctx):
    """Run every generator; keep the ones that had something to say."""
    return [ins for ins in (gen(ctx) for gen in GENERATORS) if ins]

"""Pace / time formatting, the Riegel race-time projection, and the milestone step.

Shared by planning (project a finish time), playlist (size a playlist to that
finish time) and digest (format every figure). Kept in one place so the pace
math is defined exactly once.
"""

# Runs are prescribed in whole multiples of this, everywhere. Milestones are
# what runners actually remember, and a digest suggesting 6 km runs while the
# week planner books 5 km ones reads as two coaches disagreeing.
MILESTONE_STEP_KM = 5


def to_milestone(km: float, minimum: float = MILESTONE_STEP_KM) -> float:
    """Round a distance to the nearest milestone, never below `minimum`."""
    return max(minimum, round(km / MILESTONE_STEP_KM) * MILESTONE_STEP_KM)


def fmt_pace(sec_per_km: float) -> str:
    """Seconds-per-km -> 'M:SS/km'."""
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km - m * 60))
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}/km"


def fmt_hms(seconds: float) -> str:
    """Seconds -> 'H:MM:SS' (drops the hour when zero)."""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def speed_to_pace(mps: float) -> float:
    """Metres-per-second -> seconds-per-km."""
    return (1000.0 / mps) if mps else 0.0


def riegel(t1_sec: float, d1_m: float, d2_m: float, exponent: float = 1.06) -> float:
    """Project race time for distance d2 from a known effort (t1 over d1).

    T2 = T1 * (D2 / D1) ** exponent. 1.06 is the standard endurance value;
    nudge it up for longer projections if they read optimistic.
    """
    if d1_m <= 0:
        raise ValueError("d1_m must be positive")
    return t1_sec * (d2_m / d1_m) ** exponent


def best_projection(efforts: dict, target_m: float, exponent: float = 1.06):
    """The fastest Riegel projection to target_m across every cached effort.

    Returns (finish_sec, pace_sec_per_km, anchor_m).

    Projecting from the nearest effort below the target is the textbook move,
    but Strava's per-activity "best efforts" are only the quickest stretch
    *within* that activity — an easy run contributes an easy 400 m. Taking the
    fastest projection instead lets a strong 5 km speak for the 400 m it
    implies, and lets a jogged 400 m be ignored, which is the whole point of
    having several data points.
    """
    if not efforts:
        raise ValueError("no efforts to project from")
    best_m, best_finish = min(
        ((m, riegel(s, m, target_m, exponent)) for m, s in efforts.items()),
        key=lambda pair: pair[1],
    )
    return best_finish, best_finish / (target_m / 1000.0), best_m

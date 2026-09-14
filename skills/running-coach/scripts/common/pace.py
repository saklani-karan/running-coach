"""Pace / time formatting and the Riegel race-time projection.

Shared by planning (project a finish time), playlist (size a playlist to that
finish time) and digest (format every figure). Kept in one place so the pace
math is defined exactly once.
"""


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

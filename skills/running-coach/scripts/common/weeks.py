"""Week-boundary helpers (Monday-anchored), shared across capabilities."""
from datetime import datetime, timedelta, date


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def week_key(dt: datetime):
    """Monday-anchored week-start date for a datetime."""
    return (dt - timedelta(days=dt.weekday())).date()


def week_bounds(today: date | None = None):
    """(monday, sunday) for the week containing `today` (default: today)."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)

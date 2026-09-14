"""Normalisers: raw MCP payloads -> the shapes the scripts expect.

Pure functions over JSON the agent has already fetched through the MCP tools —
no network calls and no credentials live here. Every normaliser is idempotent,
so re-ingesting an already-normalised blob is a no-op.
"""

# Strava's best-effort keys vary between the MCP response and the REST payload;
# map both onto the display labels the pipeline uses.
EFFORT_LABELS = {
    "Fastest400": "400m",
    "FastestHalfMile": "1/2 mile",
    "Fastest1k": "1K",
    "FastestMile": "1 mile",
    "Fastest2mile": "2 mile",
    "Fastest5k": "5K",
    "Fastest10k": "10K",
    "Fastest15k": "15K",
    "FastestHalfMarathon": "Half-Marathon",
    "FastestMarathon": "Marathon",
}

# Display label -> distance in metres, for Riegel projections.
EFFORT_METRES = {
    "400m": 400, "1/2 mile": 805, "1K": 1000, "1 mile": 1609,
    "2 mile": 3219, "5K": 5000, "10K": 10000, "15K": 15000,
    "Half-Marathon": 21097, "Marathon": 42195,
}

STREAM_KEYS = ("distance", "altitude", "velocity_smooth", "time")


class NormalizeError(ValueError):
    """Raised with an actionable message when a payload can't be normalised."""


def unwrap(payload, *envelope_keys):
    """Peel the envelope some MCP tools wrap their arrays in.

    Accepts the bare value, or a dict holding it under one of `envelope_keys`
    (or a lone "data"/"result" key), so the agent can pipe a tool response
    straight in without reshaping it first.
    """
    if not isinstance(payload, dict):
        return payload
    for key in (*envelope_keys, "data", "result", "results", "items"):
        if key in payload:
            return payload[key]
    return payload


def summarize_activity(a, want_polyline=False):
    """Normalise one Strava activity into the record shape the scripts read."""
    if "summary" in a and "start_local" in a:
        return a  # already normalised
    try:
        start = a["start_date_local"] if "start_date_local" in a else a["start_local"]
        rec = {
            "id": str(a["id"]),
            "name": a.get("name", "Run"),
            "description": a.get("description") or "",
            "sport_type": a.get("sport_type") or a.get("type") or "Run",
            "start_local": start.replace("Z", ""),
            "location_summary": a.get("location_summary") or a.get("location_city") or "",
            "summary": {
                "distance": a["distance"],
                "moving_time": a["moving_time"],
                "elapsed_time": a.get("elapsed_time", a["moving_time"]),
                "elevation_gain": a.get("total_elevation_gain", 0) or 0,
                "avg_speed": a.get("average_speed") or 0,
                "max_speed": a.get("max_speed", 0) or 0,
                "total_calories": a.get("calories", 0) or 0,
                "kudos_count": a.get("kudos_count", 0) or 0,
                "achievement_count": a.get("achievement_count", 0) or 0,
                "pr_count": a.get("pr_count", 0) or 0,
            },
        }
    except KeyError as exc:
        raise NormalizeError(
            f"activity is missing the required field {exc}. Expected a Strava "
            "activity with id, name, start_date_local, distance and moving_time — "
            "pass the Strava:list_activities response through unchanged."
        ) from exc
    if want_polyline:
        rec["reduced_polyline"] = (
            a.get("reduced_polyline")
            or (a.get("map") or {}).get("summary_polyline")
            or ""
        )
    return rec


def normalize_activities(payload, want_polyline=True):
    """A Strava:list_activities response -> a list of run records."""
    items = unwrap(payload, "activities")
    if not isinstance(items, list):
        raise NormalizeError(
            "expected a list of activities. Pipe the Strava:list_activities "
            f"response in as-is; got {type(items).__name__}."
        )
    runs = [
        summarize_activity(a, want_polyline)
        for a in items
        if (a.get("sport_type") or a.get("type") or "Run") == "Run"
    ]
    return sorted(runs, key=lambda r: r["start_local"])


def _effort(e):
    label = e.get("display_text") or EFFORT_LABELS.get(e.get("name", ""), e.get("name"))
    value = e.get("value", e.get("elapsed_time", e.get("moving_time")))
    if not label or value is None:
        return None
    return {"display_text": label, "value": int(value)}


def normalize_performance(payload, activity_id=None):
    """A Strava:get_activity_performance response -> {activity_id: {...}}.

    Accepts one activity's response (with `--activity-id`), a list of them, or
    an already-keyed dict, so the agent can ingest activities one at a time.
    """
    payload = unwrap(payload, "performance")
    if isinstance(payload, list):
        out = {}
        for item in payload:
            out.update(normalize_performance(item))
        return out
    if not isinstance(payload, dict):
        raise NormalizeError(f"expected an object, got {type(payload).__name__}.")
    if not payload:
        return {}

    # Already keyed by activity id?
    if all(k.isdigit() for k in payload):
        return {k: _performance_record(v) for k, v in payload.items()}

    aid = activity_id or payload.get("id") or payload.get("activity_id")
    if not aid:
        raise NormalizeError(
            "cannot tell which activity this performance payload belongs to. "
            "Re-run with --activity-id <id>."
        )
    return {str(aid): _performance_record(payload)}


def _performance_record(p):
    efforts = [_effort(e) for e in (p.get("best_efforts") or [])]
    return {
        "has_heartrate": bool(p.get("has_heartrate", False)),
        "calories": p.get("calories", 0) or 0,
        "best_efforts": [e for e in efforts if e],
    }


def normalize_streams(payload, activity_id=None):
    """A Strava:get_activity_streams response -> {activity_id: {key: [...]}}."""
    payload = unwrap(payload, "streams")
    if not isinstance(payload, dict):
        raise NormalizeError(f"expected an object, got {type(payload).__name__}.")
    if not payload:
        return {}

    if all(k.isdigit() for k in payload):
        return {k: _stream_record(v) for k, v in payload.items()}

    aid = activity_id or payload.get("id") or payload.get("activity_id")
    if not aid:
        raise NormalizeError(
            "cannot tell which activity these streams belong to. "
            "Re-run with --activity-id <id>."
        )
    return {str(aid): _stream_record(payload)}


def _stream_record(s):
    if isinstance(s, list):  # [{"type": "distance", "data": [...]}, ...]
        s = {item.get("type"): item for item in s if isinstance(item, dict)}
    out = {}
    for key, value in s.items():
        if key not in STREAM_KEYS:
            continue
        series = value.get("data") if isinstance(value, dict) else value
        if isinstance(series, list):
            out[key] = series
    missing = [k for k in ("distance", "time") if k not in out]
    if missing:
        raise NormalizeError(
            f"streams are missing {', '.join(missing)}. Request "
            "keys=distance,altitude,velocity_smooth,time from "
            "Strava:get_activity_streams."
        )
    return out


def normalize_weather(payload):
    """AccuWeather context, keyed per run. Kept as-is beyond a shape check."""
    payload = unwrap(payload, "weather")
    if not isinstance(payload, dict):
        raise NormalizeError(f"expected an object, got {type(payload).__name__}.")
    payload.setdefault("location", {})
    payload.setdefault("per_run_conditions", {})
    return payload


def normalize_athlete_profile(payload):
    """A Strava:get_athlete_profile response -> the fields the goal model uses."""
    p = unwrap(payload, "athlete", "profile")
    if not isinstance(p, dict):
        raise NormalizeError(f"expected an object, got {type(p).__name__}.")
    return {
        "firstname": p.get("firstname", ""),
        "current_focus": p.get("current_focus") or p.get("focus") or "",
        "weight": p.get("weight"),
        "raw": p,
    }


def normalize_athlete_zones(payload):
    """A Strava:get_athlete_zones response; personal bests feed the digest."""
    z = unwrap(payload, "zones")
    if not isinstance(z, dict):
        raise NormalizeError(f"expected an object, got {type(z).__name__}.")
    bests = {}
    for label, value in (z.get("personal_bests") or {}).items():
        canonical = EFFORT_LABELS.get(label, label)
        if canonical in EFFORT_METRES and value:
            bests[canonical] = int(value)
    return {"personal_bests": bests, "raw": z}


def normalize_spotify_recent(payload):
    """A Spotify:search response for recent listening -> artists + tracks."""
    payload = unwrap(payload, "tracks", "recently_played")
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        raise NormalizeError(
            "expected a list of tracks from Spotify:search; got "
            f"{type(payload).__name__}."
        )
    tracks, artists = [], []
    for item in payload:
        t = item.get("track", item) if isinstance(item, dict) else {}
        name = t.get("name")
        who = t.get("artists")
        if isinstance(who, list):
            who = [a.get("name") if isinstance(a, dict) else a for a in who]
        elif isinstance(who, str):
            who = [who]
        else:
            who = [t.get("artist")] if t.get("artist") else []
        who = [a for a in who if a]
        if name:
            tracks.append({"name": name, "artists": who})
        for a in who:
            if a not in artists:
                artists.append(a)
    return {"artists": artists, "tracks": tracks}


def normalize_playlist_state(payload):
    """The pinned run playlist, written by the playlist flow (local state)."""
    p = unwrap(payload, "playlist")
    if not isinstance(p, dict):
        raise NormalizeError(f"expected an object, got {type(p).__name__}.")
    pid = p.get("playlist_id") or p.get("id") or ""
    url = p.get("url") or (f"https://open.spotify.com/playlist/{pid}" if pid else "")
    return {
        "playlist_id": pid,
        "uri": p.get("uri") or (f"spotify:playlist:{pid}" if pid else ""),
        "url": url,
        "name": p.get("name", "Today Run's Playlist"),
        "prompt": p.get("prompt", ""),
    }

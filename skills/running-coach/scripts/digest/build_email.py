#!/usr/bin/env python3
"""
build_email.py — render the weekly digest email from assets/digest_email.html.j2.

A thin data-mapper: it turns digest_data.json (written by analyze.py) into the
template context — theme, fonts, and content blocks — and renders the Jinja2
template. To restyle, edit THEME / FONTS below or the .j2 file; to re-content,
change the data. Requires jinja2.

Usage:
    python3 scripts/digest/build_email.py
    python3 scripts/digest/build_email.py --seed 7 --json
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from common.paths import ASSETS_DIR, OUTPUT_DIR, ensure_dirs
from common.runtime import ensure_dependency

# ------------------------------------------------------------------ THEME
# The whole look is these two dicts. Swap them to re-skin the digest.
THEME = {
    "paper":   "#f5ecdd",   # background
    "panel":   "#efe3cf",   # inset panels
    "ink":     "#26201b",   # primary text
    "inksoft": "#6a5d4e",   # secondary text
    "rust":    "#a63f22",   # accent (headers, first meter)
    "olive":   "#5c6b3f",   # secondary accent (second meter)
    "hair":    "#d8c39a",   # hairline rules
    "meterbg": "#e6d8bb",   # meter track
}
FONTS = {
    # Apple-native first (renders bespoke on a Mac with no web dependency),
    # Google face as progressive enhancement, web-safe last.
    "script":  "'Pinyon Script','Snell Roundhand','Savoye LET','Apple Chancery',cursive",
    "display": "'Playfair Display','Hoefler Text','Baskerville','Palatino Linotype',Georgia,'Times New Roman',serif",
    "body":    "'EB Garamond','Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif",
    # the figures/stats face — a distinct ledger-like monospace
    "figure":  "'Space Mono','American Typewriter','Courier New',Courier,monospace",
}
METER_COLORS = [THEME["rust"], THEME["olive"]]

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]

# Funky-but-literary title/subtext, chosen by how the week landed.
TITLES = {
    "miss": [
        ("Two Runs &amp; a Downpour",
         "In which our correspondent goes out twice, blames the weather once, and is entirely believed."),
        ("The Rest Week Nobody Ordered",
         "A short chronicle of good intentions, wet pavement, and the long game."),
    ],
    "near": [
        ("A Whisper From the Finish",
         "So very nearly the whole distance — the final kilometre merely being dramatic."),
        ("The Margin of a Breath",
         "Close enough to taste it; a rounding error with a pulse."),
    ],
    "hit": [
        ("The Ledger Balances",
         "The miles were asked for, and the miles were paid in full."),
        ("A Clean Week, Handsomely Run",
         "Everything requested, delivered on foot and on time."),
    ],
}


def typo(s):
    """Straight quotes -> curly, for a hand-set feel."""
    s = re.sub(r"(?<![A-Za-z0-9])'(?=[A-Za-z])", "‘", s)
    s = s.replace("'", "’")
    s = re.sub(r'(?<![A-Za-z0-9])"(?=[A-Za-z])', "“", s)
    s = s.replace('"', "”")
    return s


def pick(pct, rng=random):
    """Title band: 100%+ is a hit, 85%+ is close enough to tease, below is a miss."""
    band = "hit" if pct >= 100 else "near" if pct >= 85 else "miss"
    return rng.choice(TITLES[band])


def run_footnote(r):
    """Best efforts and conditions, skipping whichever of them is absent."""
    parts = []
    if r.get("best_1k"):
        parts.append(f'Fastest kilometre {r["best_1k"]}')
    if r.get("best_5k"):
        parts.append(f'fastest 5k {r["best_5k"]}')
    line = ", ".join(parts)
    if line:
        line += "."
    if r.get("weather"):
        line = f'{line} {r["weather"]}.'.strip()
    return line


def build_context(d, rng=random):
    g, t = d["goal"], d["totals"]
    title, subtext = pick(g["distance_pct"], rng)
    if g["achieved"]:
        verdict = "the ledger balanced"
    elif d.get("week_in_progress"):
        days_left = d.get("days_remaining", 0)
        left = (f"{days_left} day{'s' if days_left != 1 else ''} still to run"
                if days_left else "today still to run")
        verdict = (f"the week stands {g['distance_gap_km']:.1f} km short with "
                   f"{left}")
    else:
        verdict = f"the week fell {g['distance_gap_km']:.1f} km short"

    ledger = [
        {"value": t["distance_km"], "unit": "km", "label": "Distance"},
        {"value": t["runs"],        "unit": "",   "label": "Runs"},
        {"value": t["avg_pace"].replace("/km", ""), "unit": "/km", "label": "Pace"},
        {"value": t["moving_time"], "unit": "",   "label": "Moving"},
        {"value": t["elevation_m"], "unit": "m",  "label": "Ascent"},
        {"value": t["calories"],    "unit": "",   "label": "Calories"},
    ]

    prose_rest = (f"easured against a rolling build target of <b>{g['distance_km']} km</b> across "
                  f"<b>{g['runs']} runs</b> &mdash; drawn not from thin air but from a month of your own "
                  f"mileage &mdash; {verdict}. A quiet week, honestly logged.")

    meters = [
        {"label": "Distance run", "fraction": f"{t['distance_km']} / {g['distance_km']} km",
         "pct": g["distance_pct"], "color": THEME["rust"]},
        {"label": "Days on foot", "fraction": f"{t['runs']} / {g['runs']} runs",
         "pct": g["runs_pct"], "color": THEME["olive"]},
    ]
    trailing = [{"km": f"{w['km']:.0f}", "wk": w["week_of"].split()[0]} for w in reversed(g["window"])]

    notes = []
    for r in d["runs"]:
        notes.append({
            "label": typo(r["label"]),
            "quote": typo(r["weather_note"]) if r["weather_note"] else "",
            "dateline": f'{r["date"]} &middot; {r["time_local"]}',
            "stats": (f'{r["distance_km"]} km &nbsp;&middot;&nbsp; {r["moving_time"]} &nbsp;&middot;&nbsp; '
                      f'{r["avg_pace"]} &nbsp;&middot;&nbsp; {r["elev"]} m ascent &nbsp;&middot;&nbsp; {r["calories"]} kcal'),
            "footnote": typo(run_footnote(r)),
        })

    insights = [{"roman": ROMAN[i], "title": typo(ins["title"]), "text": typo(ins["text"])}
                for i, ins in enumerate(d["insights"])]

    nw = d["next_week"]
    plan = {"label": "The Week Ahead", "headline": typo(nw["headline"]),
            "items": [typo(it) for it in nw["items"]]}

    return {
        "theme": THEME, "fonts": FONTS,
        "masthead": {"kicker": "A Weekly Running Almanac",
                     "date_range": f'{d["week_start"]} &ndash; {d["week_end"]}',
                     "title": title, "subtext": subtext},
        "ledger": ledger,
        "goal": {"prose_dropcap": "M", "prose_rest": prose_rest, "meters": meters, "trailing": trailing},
        "cities": d.get("cities", []),
        "notes": notes,
        "insights": insights,
        "plan": plan,
        "colophon": {
            "line1": f'Set on {d["generated"]} for {d["athlete"]}. &nbsp;Miles by Strava, skies by AccuWeather and the runner&rsquo;s own eye.',
            "line2": "The target is no fixed decree &mdash; it is drawn afresh each week from the mileage behind it.",
        },
    }, title


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render digest_data.json into the weekly digest HTML email.")
    ap.add_argument("--out", help=f"directory holding digest_data.json and "
                                  f"receiving the HTML (default {OUTPUT_DIR})")
    ap.add_argument("--seed", type=int,
                    help="fix the masthead title choice, for reproducible output")
    ap.add_argument("--json", action="store_true",
                    help="print {subject, path, bytes} as JSON")
    args = ap.parse_args(argv)
    ensure_dependency("jinja2", "render the digest email template")

    out_dir = Path(args.out).expanduser() if args.out else OUTPUT_DIR
    data_path = out_dir / "digest_data.json"
    if not data_path.exists():
        print(f"error: {data_path} is missing. Run "
              "`python3 scripts/digest/analyze.py` first (or the whole pipeline "
              "with scripts/digest/run_pipeline.py).", file=sys.stderr)
        return 1
    try:
        digest = json.loads(data_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"error: {data_path} is not valid JSON ({exc}). Re-run analyze.py "
              "to regenerate it.", file=sys.stderr)
        return 1

    from jinja2 import Environment, FileSystemLoader

    ctx, title = build_context(digest, random.Random(args.seed) if args.seed
                               is not None else random)
    env = Environment(loader=FileSystemLoader(str(ASSETS_DIR)), autoescape=False)
    html = env.get_template("digest_email.html.j2").render(**ctx)

    ensure_dirs(out_dir)
    path = out_dir / "weekly_run_digest.html"
    path.write_text(html)
    subject = f"The Weekly Almanac — {title.replace('&amp;', '&')}"
    if args.json:
        print(json.dumps({"subject": subject, "path": str(path),
                          "bytes": path.stat().st_size}, indent=2))
    else:
        print(f"SUBJECT: {subject}")
        print(f"Wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

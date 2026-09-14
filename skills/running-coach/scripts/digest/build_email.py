#!/usr/bin/env python3
"""
build_email.py — render the weekly digest email from assets/digest_email.html.

A thin data-mapper: it turns digest_data.json (written by analyze.py) into the
template context — theme, fonts, and content blocks — and fills the template.
To restyle, edit THEME / FONTS below or the HTML file; to re-content, change
the data.

Standard library only. The template is a string.Template, not Jinja2: the
repeating blocks are built by the _row helpers below and dropped in as single
placeholders. string.Template rather than str.format because the template
carries literal CSS braces, which str.format would require escaping throughout.

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
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from common.paths import ASSETS_DIR, OUTPUT_DIR, ensure_dirs

TEMPLATE_NAME = "digest_email.html"

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


# ------------------------------------------------------------------ RENDER
# Each block helper emits one repeating region of the template. The leading
# "\n      " and trailing "      " on each row are part of the document's
# indentation, not decoration: the template holds the first indent before the
# placeholder and each row supplies its own thereafter.
SPACER = '<div style="height:16px;"></div>'


def _hr(weight, color, margin="0"):
    return (f'<div style="border-top:{weight}px solid {color};font-size:0;'
            f'line-height:0;margin:{margin};">&nbsp;</div>')


def _figure(f, th, fo):
    unit = (f'<span style="font-family:{fo["body"]};font-size:15px;'
            f'color:{th["inksoft"]};"> {f["unit"]}</span>') if f["unit"] else ""
    return (f'<td style="padding:10px 6px;text-align:center;vertical-align:bottom;">\n'
            f'  <div style="font-family:{fo["figure"]};color:{th["ink"]};font-size:28px;'
            f'font-weight:700;line-height:1;letter-spacing:-.02em;">{f["value"]}{unit}</div>\n'
            f'  <div style="font-family:{fo["body"]};color:{th["inksoft"]};font-size:12px;'
            f'letter-spacing:.16em;text-transform:uppercase;margin-top:9px;">{f["label"]}</div>\n'
            f'</td>')


def _meter(m, th, fo):
    width = max(2, min(m["pct"], 100))
    return (f'<table role="presentation" width="100%" style="border-collapse:collapse;margin-bottom:4px;">\n'
            f'  <tr>\n'
            f'    <td style="font-family:{fo["body"]};color:{th["ink"]};font-size:16px;">{m["label"]}</td>\n'
            f'    <td align="right" style="font-family:{fo["figure"]};color:{th["ink"]};'
            f'font-size:15px;">{m["fraction"]}\n'
            f'      <span style="color:{m["color"]};font-size:13px;">&nbsp;({m["pct"]}%)</span></td>\n'
            f'  </tr>\n'
            f'</table>\n'
            f'<div style="height:9px;background:{th["meterbg"]};border:1px solid {th["hair"]};">\n'
            f'  <div style="height:9px;background:{m["color"]};width:{width}%;'
            f'font-size:0;line-height:0;">&nbsp;</div>\n'
            f'</div>')


def _trailing(weeks, th, fo):
    return " &nbsp;&middot;&nbsp; ".join(
        f'<span style="font-family:{fo["figure"]};color:{th["ink"]};font-size:16px;">{w["km"]}</span>'
        f'<span style="font-family:{fo["body"]};color:{th["inksoft"]};font-size:11px;"> {w["wk"]}</span>'
        for w in weeks)


def _city_rows(cities, th, fo):
    rows = []
    for i, c in enumerate(cities):
        region = (f'<span style="font-family:{fo["body"]};color:{th["inksoft"]};'
                  f'font-size:14px;font-style:italic;">, {c["region"]}</span>') if c.get("region") else ""
        rule = "" if i == len(cities) - 1 else f'<tr><td colspan="2">{_hr(1, th["hair"])}</td></tr>'
        rows.append(
            f'\n      <tr>\n'
            f'        <td style="font-family:{fo["display"]};color:{th["ink"]};'
            f'font-size:22px;padding:8px 0;">{c["name"]}{region}</td>\n'
            f'        <td align="right" style="font-family:{fo["figure"]};color:{th["ink"]};'
            f'font-size:13px;white-space:nowrap;">{c["runs"]} '
            f'{"run" if c["runs"] == 1 else "runs"} &nbsp;&middot;&nbsp; {c["distance_km"]} km</td>\n'
            f'      </tr>\n'
            f'      {rule}\n      ')
    return "".join(rows)


def _note_rows(notes, th, fo):
    rows = []
    for i, n in enumerate(notes):
        quote = (f' <span style="color:{th["rust"]};">&ldquo;{n["quote"]}&rdquo;</span>') if n["quote"] else ""
        rule = "" if i == len(notes) - 1 else f'<tr><td>{_hr(1, th["hair"])}</td></tr>'
        rows.append(
            f'\n      <tr><td style="padding:14px 0;">\n'
            f'        <table role="presentation" width="100%"><tr>\n'
            f'          <td style="font-family:{fo["display"]};color:{th["ink"]};'
            f'font-size:19px;font-style:italic;">{n["label"]}{quote}</td>\n'
            f'          <td align="right" style="font-family:{fo["body"]};color:{th["inksoft"]};'
            f'font-size:12px;letter-spacing:.12em;text-transform:uppercase;'
            f'white-space:nowrap;">{n["dateline"]}</td>\n'
            f'        </tr></table>\n'
            f'        <div style="font-family:{fo["body"]};color:{th["ink"]};'
            f'font-size:16px;margin-top:8px;">{n["stats"]}</div>\n'
            f'        <div style="font-family:{fo["body"]};color:{th["inksoft"]};'
            f'font-size:14.5px;font-style:italic;margin-top:5px;">{n["footnote"]}</div>\n'
            f'      </td></tr>\n'
            f'      {rule}\n      ')
    return "".join(rows)


def _insight_rows(insights, th, fo):
    return "".join(
        f'\n      <tr><td style="padding:12px 0;vertical-align:top;">\n'
        f'        <table role="presentation" width="100%"><tr>\n'
        f'          <td width="46" style="font-family:{fo["display"]};color:{th["rust"]};'
        f'font-size:26px;font-style:italic;vertical-align:top;line-height:1;">{i["roman"]}.</td>\n'
        f'          <td>\n'
        f'            <div style="font-family:{fo["display"]};color:{th["ink"]};'
        f'font-size:18px;font-weight:700;">{i["title"]}</div>\n'
        f'            <div style="font-family:{fo["body"]};color:{th["ink"]};'
        f'font-size:16px;line-height:1.6;margin-top:3px;">{i["text"]}</div>\n'
        f'          </td>\n'
        f'        </tr></table>\n'
        f'      </td></tr>\n      '
        for i in insights)


def _plan_items(items, th, fo):
    return "".join(
        f'\n        <tr>\n'
        f'          <td width="26" style="font-family:{fo["display"]};color:{th["rust"]};'
        f'font-size:17px;vertical-align:top;line-height:1.5;">&mdash;</td>\n'
        f'          <td style="font-family:{fo["body"]};color:{th["ink"]};font-size:16px;'
        f'line-height:1.55;padding-bottom:9px;">{it}</td>\n'
        f'        </tr>\n        '
        for it in items)


def render(ctx, assets_dir=None):
    """Fill the HTML template from the context built by build_context()."""
    th, fo = ctx["theme"], ctx["fonts"]
    g, m, p = ctx["goal"], ctx["masthead"], ctx["plan"]
    path = Path(assets_dir or ASSETS_DIR) / TEMPLATE_NAME
    # The template file ends in a newline, as a text file should; the rendered
    # document should not inherit it as a trailing blank line.
    body = path.read_text().removesuffix("\n")
    return Template(body).substitute(
        paper=th["paper"], panel=th["panel"], ink=th["ink"],
        inksoft=th["inksoft"], rust=th["rust"], hair=th["hair"],
        f_script=fo["script"], f_display=fo["display"],
        f_body=fo["body"], f_figure=fo["figure"],
        kicker=m["kicker"], date_range=m["date_range"],
        title=m["title"], subtext=m["subtext"],
        ledger_row_1="".join(_figure(f, th, fo) for f in ctx["ledger"][:3]),
        ledger_row_2="".join(_figure(f, th, fo) for f in ctx["ledger"][3:6]),
        prose_dropcap=g["prose_dropcap"], prose_rest=g["prose_rest"],
        meters=SPACER.join(_meter(x, th, fo) for x in g["meters"]),
        trailing=_trailing(g["trailing"], th, fo),
        city_rows=_city_rows(ctx["cities"], th, fo),
        note_rows=_note_rows(ctx["notes"], th, fo),
        insight_rows=_insight_rows(ctx["insights"], th, fo),
        plan_label=p["label"], plan_headline=p["headline"],
        plan_items=_plan_items(p["items"], th, fo),
        colophon1=ctx["colophon"]["line1"], colophon2=ctx["colophon"]["line2"],
    )


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

    ctx, title = build_context(digest, random.Random(args.seed) if args.seed
                               is not None else random)
    html = render(ctx)

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

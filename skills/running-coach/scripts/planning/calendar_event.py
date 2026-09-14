#!/usr/bin/env python3
"""
calendar_event.py — the contract for a run's calendar description, and its renderer.

Every word in a calendar block is written by the agent, not by this file. What
lives here is the *interface*: which variables `assets/calendar_event.tmpl`
expects, what type each one is, and what each is for. Nothing is derived,
defaulted or invented — no canned "why this run" strings, no stock jokes. Ask
for the contract, write the content against it, render.

    python3 scripts/planning/calendar_event.py --schema           # what to write
    python3 scripts/planning/calendar_event.py --content c.json   # render it
    python3 scripts/planning/calendar_event.py --placeholders --html  # layout only

`plan_week.py` supplies the facts of each run (distance, pace window, estimated
time, where it sits in the week) through its `brief` stage; the agent turns
those facts into the prose this contract asks for, and `plan_week.py events
--content` renders the result into calendar payloads.

Why the split: the numbers must be reproducible, so they are computed. The
words must be about *this* runner in *this* week, so they are written. A
hardcoded pep talk is the same pep talk forever, which is worse than none.
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
from common.paths import ASSETS_DIR

TEMPLATE_NAME = "calendar_event.tmpl"

STRING = "string"
STRING_LIST = "string[]"


class DescriptionError(Exception):
    """A description could not be rendered — bad content, or a bad template."""


@dataclass(frozen=True)
class Variable:
    """One template placeholder: what it is called, its type, and its job."""
    name: str
    type: str
    required: bool
    reason: str
    example: str


# The contract. Every one of these is filled by the agent; this file supplies
# none of the values. Order is the order they appear in the template.
VARIABLES = (
    Variable(
        "title", STRING, True,
        "The block's headline, and the first thing read in the calendar. Name "
        "the session and its distance so the day is legible at a glance.",
        "Long run — 10 km",
    ),
    Variable(
        "metrics", STRING, True,
        "The numbers to run to, straight from the plan: pace window, estimated "
        "time, effort band. Quote the plan's figures, do not recompute them.",
        "Pace 6:04–6:24/km · est. 1:00:39–1:03:59 · easy effort",
    ),
    Variable(
        "structure", STRING, True,
        "How to actually run it, in one line — warm-up, the working part, "
        "cool-down. For an easy or long run this is the effort instruction.",
        "Steady and conversational throughout — time on feet, not pace",
    ),
    Variable(
        "progress", STRING, False,
        "Where this run sits in the week, so a single block carries the shape "
        "of the whole plan. Omit when rendering a run with no week around it.",
        "Run 5 of 5 this week · 30 of 30 km",
    ),
    Variable(
        "why", STRING, True,
        "Why this session earns its place in THIS week — what it develops and "
        "what it sets up. Specific to the run and the athlete's current block, "
        "not a textbook definition of the run type.",
        "Your first 10 km since August, and the run that turns four weeks of "
        "5 km repeats into actual endurance. Everything else this week is "
        "preparation for this.",
    ),
    Variable(
        "prep", STRING_LIST, True,
        "What to sort out before setting off — conditions, warm-up, fuel, kit. "
        "One line each, concrete and scaled to this run's distance, effort and "
        "time of day. Use the cached forecast when there is one.",
        ["<b>Weather</b> — ~22°C at the start, humid; 60% chance of a storm later",
         "<b>Warm-up</b> — 5 min easy jog, then leg swings and 3–4 short strides",
         "<b>Fuel</b> — carry water; a gel around 40 minutes in"],
    ),
    Variable(
        "checklist", STRING_LIST, True,
        "Tick items for the morning, in the order they happen. Short imperative "
        "phrases. Include what this run specifically needs and leave out what "
        "it does not.",
        ["☐ Shoes + socks on", "☐ Water + fuel packed",
         "☐ Watch started", "☐ Playlist queued"],
    ),
    Variable(
        "pep", STRING, True,
        "One sentence of real encouragement, in this runner's register. Earn it "
        "from something true about their week — a streak, a comeback, a target "
        "in reach. No exclamation marks, no slogans.",
        "Four weeks of showing up bought you this distance. Go collect it.",
    ),
    Variable(
        "quip", STRING, True,
        "One dry, funny line about this kind of run. Affectionate about the "
        "absurdity of the sport, never at the runner's expense.",
        "Less a run, more a small expedition. Snacks optional, ego not required.",
    ),
    Variable(
        "footer", STRING, True,
        "The closing small print — which plan this block belongs to, so a "
        "stray event is traceable back to the week it came from.",
        "Part of your 30 km · 5-run week — planned by your running coach",
    ),
)

BY_NAME = {v.name: v for v in VARIABLES}
REQUIRED = tuple(v.name for v in VARIABLES if v.required)


# ------------------------------------------------------------------ template
def load_template():
    """The description layout as a one-line string.

    The asset is written multi-line for editing; each newline collapses to a
    single space so only the literal <br> tags break lines. A space rather than
    nothing, so prose wrapped across two lines does not run together — then the
    spaces either side of a <br> go again, since those never meant anything.
    """
    path = ASSETS_DIR / TEMPLATE_NAME
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise DescriptionError(
            f"{path} is missing — the calendar description template ships in "
            f"the skill's assets/ directory."
        ) from None
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    return re.sub(r"\s*<br>\s*", "<br>", text).strip()


def _known():
    return ", ".join("$" + v.name for v in VARIABLES)


def render_template(values, template=None):
    """Substitute `values` into the template, reporting a bad edit clearly."""
    text = template if template is not None else load_template()
    try:
        return Template(text).substitute(values)
    except KeyError as exc:
        raise DescriptionError(
            f"{TEMPLATE_NAME} uses ${exc.args[0]}, which is not a variable this "
            f"contract defines. Available: {_known()}. Add it to VARIABLES in "
            f"calendar_event.py if the block is meant to exist."
        ) from None
    except ValueError as exc:
        raise DescriptionError(
            f"{TEMPLATE_NAME} has a stray '$' ({exc}). Write '$$' for a literal "
            f"dollar sign, or use one of {_known()}."
        ) from None


# ------------------------------------------------------------------ contract
def validate(content):
    """Check agent-written content against the contract; return clean values.

    Raises DescriptionError naming every problem at once, so one round trip is
    enough to fix the content rather than one error at a time.
    """
    if not isinstance(content, dict):
        raise DescriptionError(
            f"expected an object of variables, got {type(content).__name__}. "
            f"See `calendar_event.py --schema` for the shape."
        )

    problems = []
    unknown = [k for k in content if k not in BY_NAME]
    if unknown:
        problems.append(
            f"unknown variable(s) {', '.join(sorted(unknown))} — the contract "
            f"defines {', '.join(v.name for v in VARIABLES)}")

    values = {}
    for var in VARIABLES:
        given = content.get(var.name)
        if given is None or given == "" or given == []:
            if var.required:
                problems.append(f"{var.name} is required: {var.reason}")
            values[var.name] = ""
            continue
        if var.type == STRING_LIST:
            if isinstance(given, str):
                problems.append(
                    f"{var.name} must be a list of strings, not one string — "
                    f"one entry per line")
                continue
            if not isinstance(given, list) or not all(isinstance(x, str) for x in given):
                problems.append(f"{var.name} must be a list of strings")
                continue
            values[var.name] = "<br>".join(x.strip() for x in given if x.strip())
        else:
            if not isinstance(given, str):
                problems.append(
                    f"{var.name} must be a string, got {type(given).__name__}")
                continue
            values[var.name] = given.strip()

    if problems:
        raise DescriptionError("; ".join(problems))
    return values


def render(content, template=None):
    """Agent-written content -> the HTML description a calendar event carries."""
    return render_template(validate(content), template)


def schema(as_json=False):
    """The contract itself, for an agent to write against."""
    if as_json:
        return {
            "template": TEMPLATE_NAME,
            "note": "Every variable is written by the agent. Nothing is "
                    "defaulted or derived. Quote the plan's numbers verbatim.",
            "variables": [
                {"name": v.name, "type": v.type, "required": v.required,
                 "reason": v.reason, "example": v.example}
                for v in VARIABLES
            ],
        }
    lines = [f"{TEMPLATE_NAME} — every variable is yours to write.", ""]
    for v in VARIABLES:
        req = "required" if v.required else "optional"
        lines.append(f"${v.name}  ({v.type}, {req})")
        lines.append(f"    {v.reason}")
        example = v.example if isinstance(v.example, str) else \
            "\n              ".join(v.example)
        lines.append(f"    e.g.  {example}")
        lines.append("")
    lines.append("Write these as a JSON object and render it with --content.")
    return "\n".join(lines)


def placeholder_content():
    """Obviously-fake values, for working on the template's layout alone."""
    return {
        v.name: ([f"[{v.name} line 1]", f"[{v.name} line 2]"]
                 if v.type == STRING_LIST else f"[{v.name}]")
        for v in VARIABLES
    }


# --------------------------------------------------------------- CLI helpers
HTML_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<meta charset="utf-8">
<title>$title</title>
<style>
  body { background: #f1f3f4; margin: 0; padding: 32px;
         font-family: Roboto, -apple-system, "Segoe UI", Arial, sans-serif;
         color: #3c4043; }
  .card { max-width: 480px; margin: 0 auto 24px; background: #fff;
          border-radius: 8px; padding: 20px 24px;
          box-shadow: 0 1px 3px rgba(60,64,67,.3); }
  h1 { font-size: 22px; font-weight: 400; margin: 0 0 16px; }
  .desc { font-size: 14px; line-height: 1.5; }
  .note { max-width: 480px; margin: 0 auto 16px; font-size: 12px; color: #5f6368; }
</style>
<div class="note">Preview only — Google Calendar allows a limited HTML subset
(b, i, u, br, ul, li, a) and applies its own fonts.</div>
$cards
</html>
""")

HTML_CARD = Template("""<div class="card">
  <h1>$summary</h1>
  <div class="desc">$description</div>
</div>
""")


def read_json_arg(value):
    """Read a JSON document from stdin ('-') or a file path."""
    if value == "-":
        if sys.stdin.isatty():
            raise SystemExit("nothing on stdin to read the content from")
        return json.loads(sys.stdin.read())
    path = Path(value).expanduser()
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    return json.loads(path.read_text())


def content_items(doc):
    """Normalise a content document into an ordered list of (label, values).

    Accepts one object, a list of objects, or an object keyed by anything
    meaningful to the caller (a date, a run index) whose values are objects.
    """
    if isinstance(doc, list):
        return [(str(i), c) for i, c in enumerate(doc)]
    if isinstance(doc, dict):
        if all(isinstance(v, dict) for v in doc.values()) and doc:
            return sorted(doc.items())
        return [("", doc)]
    raise SystemExit("expected a content object, a list of them, or an object "
                     "keyed by date")


def as_plain_text(html):
    """The description as it reads, not as it is sent: tags out, breaks in."""
    text = re.sub(r"<br>", "\n", html)
    text = re.sub(r"</?(?:b|i|u|small)>", "", text)
    return text.replace("&nbsp;", " ")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="The contract for a run's calendar description, and its renderer.",
        epilog="Every variable is agent-written; this script derives nothing. "
               "Start with --schema, write the JSON, render it with --content.",
    )
    ap.add_argument("--schema", action="store_true",
                    help="print the variables to write, with types and reasons")
    ap.add_argument("--content", metavar="FILE|-",
                    help="the written variables: one JSON object, a list of "
                         "them, or an object keyed by date")
    ap.add_argument("--placeholders", action="store_true",
                    help="render with bracketed markers instead of content, to "
                         "work on the template's layout")
    ap.add_argument("--summary", help="event title, shown only in --html/--text chrome")

    out = ap.add_argument_group("output")
    out.add_argument("--text", action="store_true",
                     help="breaks as newlines and tags stripped, for reading "
                          "in the terminal")
    out.add_argument("--html", action="store_true",
                     help="a minimal page wrapping the description, for a browser")
    out.add_argument("--json", action="store_true", dest="as_json",
                     help="the rendered description(s) as JSON")

    args = ap.parse_args(argv)
    if sum(bool(x) for x in (args.text, args.html, args.as_json)) > 1:
        ap.error("choose one of --text, --html, --json")

    if args.schema:
        print(json.dumps(schema(as_json=True), indent=2, ensure_ascii=False)
              if args.as_json else schema())
        return 0

    if args.placeholders:
        items = [("placeholders", placeholder_content())]
    elif args.content:
        items = content_items(read_json_arg(args.content))
    else:
        ap.error("nothing to render: pass --content, or --placeholders to see "
                 "the layout, or --schema to see what to write")

    try:
        rendered = [(label, render(values)) for label, values in items]
    except DescriptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(
            [{"key": k, "description": html} for k, html in rendered],
            indent=2, ensure_ascii=False))
        return 0

    if args.html:
        cards = "".join(
            HTML_CARD.substitute(summary=args.summary or k or "Run",
                                 description=html)
            for k, html in rendered)
        print(HTML_PAGE.substitute(
            title=args.summary or f"{len(rendered)} calendar block(s)", cards=cards))
        return 0

    for i, (k, html) in enumerate(rendered):
        if i:
            print()
        if args.text:
            if args.summary or k:
                print(args.summary or k)
                print()
            print(as_plain_text(html))
        else:
            print(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())

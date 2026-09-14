#!/usr/bin/env python3
"""
run_pipeline.py — one command to build the whole weekly run digest.

    1. validate     the cache holds a usable week (scripts/cache.py)
    2. analyze      weekly totals, rolling goal, cities, insights
    3. build_email   render assets/digest_email.html

Nothing here fetches anything. Refresh the cache first with
`python3 scripts/cache.py status`, which names the MCP call for each gap.

Usage:
    python3 scripts/digest/run_pipeline.py
    python3 scripts/digest/run_pipeline.py --plan-km 8       # lead with tonight's run
    python3 scripts/digest/run_pipeline.py --seed 3 --out /tmp/digest
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
import analyze
import build_email
import cache
from common.paths import DATA_DIR, OUTPUT_DIR


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Validate the cache, analyse the week, render the digest email.")
    ap.add_argument("--data", help=f"cache directory (default {DATA_DIR})")
    ap.add_argument("--out", help=f"output directory (default {OUTPUT_DIR})")
    ap.add_argument("--seed", type=int, help="fix the masthead title choice")
    ap.add_argument("--plan", metavar="FILE",
                    help="a run plan from `plan_run.py --json` (- for stdin) to "
                         "lead the next-week section")
    ap.add_argument("--plan-km", type=float, metavar="KM",
                    help="project a planned run of this distance and lead with it")
    ap.add_argument("--start-in", type=float, default=0, metavar="HOURS",
                    help="hours until the --plan-km run starts, so its effort "
                         "band matches what planning reported")
    ap.add_argument("--skip-validate", action="store_true",
                    help="go straight to analysis (analysis still fails loudly)")
    args = ap.parse_args(argv)

    common = ["--data", args.data] if args.data else []
    out = ["--out", args.out] if args.out else []
    if args.plan:
        plan = ["--plan", args.plan]
    elif args.plan_km:
        plan = ["--plan-km", str(args.plan_km), "--start-in", str(args.start_in)]
    else:
        plan = []

    def step(name, result, recovery):
        """Print each step's banner and, on failure, what is already done."""
        print(f"=== {name} ===", flush=True)
        if result() == 0:
            print(flush=True)
            return True
        print(f"\n{name} failed. {recovery}", file=sys.stderr)
        return False

    if not args.skip_validate:
        if not step("validate", lambda: cache.main([*common, "validate", "--for", "digest"]),
                    "Nothing has been written. Run `python3 scripts/cache.py status` "
                    "for the exact MCP calls, or pass --skip-validate to try anyway."):
            return 1

    if not step("analyze", lambda: analyze.main([*common, *out, *plan]),
                "Nothing has been written."):
        return 1

    seed = ["--seed", str(args.seed)] if args.seed is not None else []
    if not step("build_email", lambda: build_email.main([*out, *seed]),
                "digest_data.json is already written, so only the render needs "
                "redoing: re-run `python3 scripts/digest/build_email.py`."):
        return 1

    print("Done. The HTML file is self-contained and ready to send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

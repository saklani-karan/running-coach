#!/usr/bin/env python3
"""Build the distributable archives and check them against the size limits.

Two targets, because the repo ships two things:

    python3 package.py --plugin   the Claude plugin (Cowork file upload)
    python3 package.py --skill    the bare skill (claude.ai, OpenClaw, ...)
    python3 package.py            both

Neither is needed for the GitHub route: Cowork and Claude Code read
.claude-plugin/marketplace.json straight from the repo, and any agent that
scans a skills root can read skills/running-coach in place.

Archives land in dist/.
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILL = REPO / "skills" / "running-coach"

# Cowork's documented per-package ceilings. The bare skill is far smaller than
# either, and well under claude.ai's limit too.
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_FILES = 5000

# Junk, wherever it appears.
EXCLUDE_ANYWHERE = {"__pycache__", ".venv", "venv", ".git"}
# Top-level only, so the skill's own scripts/ directory survives. These exist
# because the plugin root is the repo root, so anything at the root would
# otherwise ship: evals/ is how the skill is tested, dist/ is this script's own
# output.
EXCLUDE_TOP = {"evals", "dist"}
# marketplace.json is deliberately out of the uploaded package: the repo serves
# it for the GitHub route, but inside a plugin archive it invites Cowork to read
# the upload as a marketplace rather than a plugin. package.py builds the
# archive; it has no business inside one.
EXCLUDE_NAMES = {".DS_Store", ".gitignore", ".plugin-data-dir",
                 "marketplace.json", "ROADMAP.md", "package.py"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def members(root, top_level_excludes):
    """Every file that belongs in an archive, as (path, relative path) pairs."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if EXCLUDE_ANYWHERE & set(rel.parts):
            continue
        if rel.parts[0] in top_level_excludes:
            continue
        if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
            continue
        yield path, rel


def build(out, root, top_level_excludes):
    """Write the archive with everything under a single `running-coach/` root."""
    out.parent.mkdir(parents=True, exist_ok=True)
    files = list(members(root, top_level_excludes))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in files:
            zf.write(path, Path("running-coach") / rel)
    return {
        "archive": str(out),
        "files": len(files),
        "uncompressed_bytes": sum(p.stat().st_size for p, _ in files),
        "compressed_bytes": out.stat().st_size,
    }


def report(label, result):
    ok = (result["files"] <= MAX_FILES
          and result["uncompressed_bytes"] <= MAX_UNCOMPRESSED)
    result["within_limits"] = ok
    print(f"{label}: {result['archive']}")
    print(f"  files        : {result['files']} (limit {MAX_FILES})")
    print(f"  uncompressed : {result['uncompressed_bytes'] / 1024:.1f} KB "
          f"(limit {MAX_UNCOMPRESSED // (1024 * 1024)} MB)")
    print(f"  compressed   : {result['compressed_bytes'] / 1024:.1f} KB")
    if not ok:
        print(f"error: {label} exceeds the limits", file=sys.stderr)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plugin", action="store_true", help="build the plugin only")
    ap.add_argument("--skill", action="store_true", help="build the bare skill only")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = ap.parse_args(argv)
    both = not (args.plugin or args.skill)

    manifest = REPO / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        print(f"error: no plugin manifest at {manifest}", file=sys.stderr)
        return 1
    version = json.loads(manifest.read_text()).get("version", "0.0.0")
    out_dir = REPO / "dist"

    results = {}
    if args.plugin or both:
        results["plugin"] = build(
            out_dir / f"running-coach-plugin-{version}.zip", REPO, EXCLUDE_TOP)
    if args.skill or both:
        results["skill"] = build(
            out_dir / f"running-coach-skill-{version}.zip", SKILL, set())

    if args.json:
        for r in results.values():
            r["within_limits"] = (r["files"] <= MAX_FILES
                                  and r["uncompressed_bytes"] <= MAX_UNCOMPRESSED)
        print(json.dumps({"version": version, **results}, indent=2))
    else:
        for label, r in results.items():
            report(label, r)
            print()
    return 0 if all(r.get("within_limits", True) for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate the skill and build the uploadable archive.

    python3 package.py              validate, then write dist/running-coach.zip
    python3 package.py --validate   validate only
    python3 package.py --json       machine-readable result

The archive holds a single `running-coach/` directory at its root, which is
what claude.ai, the Skills API and Claude Code all expect; a zip of loose
files at the root is rejected. Some clients prefer the `.skill` extension —
that is the same archive under a different name, so just rename it.

Only the skill directory is packaged, so the repo's README, evals and this
script stay out of the artifact without needing to be excluded by name.

This mirrors the checks in anthropics/skills `quick_validate.py` and the
archive layout of its `package_skill.py`, reimplemented here so the repo needs
no third-party packages, matching the skill itself.
"""
import argparse
import fnmatch
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILL = REPO / "skills" / "running-coach"

# Cowork's documented per-package ceilings; the skill is orders of magnitude
# under both, so this is a regression guard rather than a real constraint.
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_FILES = 5000

# claude.ai truncates the description in its picker. Not a spec limit, so this
# is a warning rather than an error.
CLAUDE_AI_DESCRIPTION_LIMIT = 200

ALLOWED_KEYS = {"name", "description", "license", "allowed-tools",
                "metadata", "compatibility"}

EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git"}
EXCLUDE_FILES = {".DS_Store"}
EXCLUDE_GLOBS = {"*.pyc", "*.pyo"}


def parse_frontmatter(text):
    """Return the top-level frontmatter keys as strings.

    Covers the subset the spec allows for a skill: plain scalars and `>-`/`|`
    block scalars. Anything else raises, so an unparseable file fails the
    build rather than slipping through unchecked.
    """
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("no YAML frontmatter found")

    fields, key, block = {}, None, []
    for line in match.group(1).splitlines():
        if key and (line.startswith((" ", "\t")) or not line.strip()):
            block.append(line.strip())
            continue
        if key:
            fields[key] = " ".join(b for b in block if b)
            key, block = None, []
        if not line.strip():
            continue
        head, sep, value = line.partition(":")
        if not sep or head != head.strip():
            raise ValueError(f"cannot parse frontmatter line: {line!r}")
        value = value.strip()
        if value in (">-", ">", "|", "|-"):
            key = head.strip()
        else:
            fields[head.strip()] = value.strip("'\"")
    if key:
        fields[key] = " ".join(b for b in block if b)
    return fields


def validate(skill_path):
    """Return (errors, warnings, fields) for the skill at skill_path."""
    errors, warnings = [], []
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return [f"SKILL.md not found in {skill_path}"], [], {}

    try:
        fields = parse_frontmatter(skill_md.read_text())
    except ValueError as exc:
        return [str(exc)], [], {}

    unexpected = set(fields) - ALLOWED_KEYS
    if unexpected:
        errors.append(f"unexpected frontmatter key(s): {', '.join(sorted(unexpected))}; "
                      f"allowed: {', '.join(sorted(ALLOWED_KEYS))}")

    name = fields.get("name", "")
    if not name:
        errors.append("missing 'name' in frontmatter")
    else:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            errors.append(f"name {name!r} must be kebab-case (lowercase, digits, single hyphens)")
        if len(name) > 64:
            errors.append(f"name is {len(name)} characters; the limit is 64")
        if name != skill_path.name:
            errors.append(f"name {name!r} must match the directory name {skill_path.name!r}")

    description = fields.get("description", "")
    if not description:
        errors.append("missing 'description' in frontmatter")
    else:
        if "<" in description or ">" in description:
            errors.append("description cannot contain angle brackets")
        if len(description) > 1024:
            errors.append(f"description is {len(description)} characters; the limit is 1024")
        elif len(description) > CLAUDE_AI_DESCRIPTION_LIMIT:
            warnings.append(f"description is {len(description)} characters; "
                            f"claude.ai shows about {CLAUDE_AI_DESCRIPTION_LIMIT}")

    compatibility = fields.get("compatibility", "")
    if len(compatibility) > 500:
        errors.append(f"compatibility is {len(compatibility)} characters; the limit is 500")

    return errors, warnings, fields


def members(root):
    """Every file that belongs in the archive, as (path, arcname) pairs."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if EXCLUDE_DIRS & set(rel.parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        if any(fnmatch.fnmatch(path.name, pat) for pat in EXCLUDE_GLOBS):
            continue
        yield path, Path(root.name) / rel


def build(root, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    files = list(members(root))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in files:
            zf.write(path, arcname)
    return {
        "archive": str(out),
        "root": f"{root.name}/",
        "files": len(files),
        "uncompressed_bytes": sum(p.stat().st_size for p, _ in files),
        "compressed_bytes": out.stat().st_size,
        "within_limits": (len(files) <= MAX_FILES
                          and sum(p.stat().st_size for p, _ in files) <= MAX_UNCOMPRESSED),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", action="store_true", help="validate without building")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = ap.parse_args(argv)

    errors, warnings, fields = validate(SKILL)
    if not args.json:
        for w in warnings:
            print(f"warning: {w}")
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
    if errors:
        if args.json:
            print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, indent=2))
        return 1

    if args.validate:
        if args.json:
            print(json.dumps({"valid": True, "warnings": warnings, **fields}, indent=2))
        else:
            print(f"{SKILL.name} is valid "
                  f"({len(fields.get('description', ''))}-character description).")
        return 0

    result = build(SKILL, REPO / "dist" / f"{SKILL.name}.zip")
    if args.json:
        print(json.dumps({"valid": True, "warnings": warnings, **result}, indent=2))
    else:
        print(f"Wrote {result['archive']}")
        print(f"  archive root : {result['root']}")
        print(f"  files        : {result['files']} (limit {MAX_FILES})")
        print(f"  uncompressed : {result['uncompressed_bytes'] / 1024:.1f} KB "
              f"(limit {MAX_UNCOMPRESSED // (1024 * 1024)} MB)")
        print(f"  compressed   : {result['compressed_bytes'] / 1024:.1f} KB")
    return 0 if result["within_limits"] else 1


if __name__ == "__main__":
    sys.exit(main())

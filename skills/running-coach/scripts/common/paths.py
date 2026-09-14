"""Path resolution shared by all running-coach scripts.

Scripts live in <skill>/scripts/<capability>/. The cache and generated output
live OUTSIDE the skill so the shipped copy stays read-only, and so a skill
installed somewhere read-only still works:

    1. $RUNNING_COACH_DATA / $RUNNING_COACH_OUTPUT, if set
    2. ~/.running-coach/{data,output} otherwise

Working from a clone resolves the same way as any other copy, which keeps the
repo pure source and means the cache is not tied to one checkout.

On a per-session filesystem, such as a hosted code-execution sandbox, the
cache starts empty each session and the agent refills it through cache.py.
Nothing here assumes the cache survives.

Nothing here creates directories at import time; call ensure_dirs() first.
"""
import os
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]   # .../scripts
SKILL_ROOT = SCRIPTS_DIR.parent                     # .../running-coach
ASSETS_DIR = SKILL_ROOT / "assets"

HOME_ROOT = Path.home() / ".running-coach"


def _resolve(env_var, subdir):
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser()
    return HOME_ROOT / subdir


DATA_DIR = _resolve("RUNNING_COACH_DATA", "data")
OUTPUT_DIR = _resolve("RUNNING_COACH_OUTPUT", "output")


def ensure_dirs(*dirs):
    """Create the given directories (default: data + output) and return them."""
    dirs = dirs or (DATA_DIR, OUTPUT_DIR)
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    return dirs

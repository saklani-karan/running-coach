"""Path resolution shared by all running-coach scripts.

Scripts live in <skill>/scripts/<capability>/. The cache and generated output
live OUTSIDE the skill so the shipped copy stays read-only. The lookup order is
explicit rather than best-effort, so a skill with nothing around it still
resolves to a real, writable directory:

    1. $RUNNING_COACH_DATA / $RUNNING_COACH_OUTPUT, if set
    2. the plugin's persistent data directory, when installed as a plugin —
       that directory is the one place that survives a plugin update, whereas
       the plugin's own install directory is replaced
    3. ~/.running-coach/{data,output} otherwise

Step 2 has to work even though scripts run through the Bash tool, which is not
promised $CLAUDE_PLUGIN_DATA in its environment. The SessionStart hook, which
is, drops the path into .plugin-data-dir at the plugin root, so the answer is
on disk whether or not it is in the environment.

There is deliberately no "am I in the source repo?" case. Working from a clone
resolves to ~/.running-coach like any other uninstalled copy, which keeps the
repo pure source and means the cache is not tied to one checkout.

Nothing here creates directories at import time; call ensure_dirs() first.
"""
import os
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]   # .../scripts
SKILL_ROOT = SCRIPTS_DIR.parent                     # .../running-coach
ASSETS_DIR = SKILL_ROOT / "assets"

HOME_ROOT = Path.home() / ".running-coach"


def plugin_data_root():
    """The plugin's persistent data directory, when running as a plugin."""
    value = os.environ.get("CLAUDE_PLUGIN_DATA")
    if value:
        return Path(value).expanduser()
    for p in [SKILL_ROOT, *SKILL_ROOT.parents]:
        marker = p / ".plugin-data-dir"
        if marker.is_file():
            recorded = marker.read_text().strip()
            if recorded:
                return Path(recorded).expanduser()
    return None


def _resolve(env_var, subdir):
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser()
    plugin = plugin_data_root()
    if plugin:
        return plugin / subdir
    return HOME_ROOT / subdir


DATA_DIR = _resolve("RUNNING_COACH_DATA", "data")
OUTPUT_DIR = _resolve("RUNNING_COACH_OUTPUT", "output")


def ensure_dirs(*dirs):
    """Create the given directories (default: data + output) and return them."""
    dirs = dirs or (DATA_DIR, OUTPUT_DIR)
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    return dirs

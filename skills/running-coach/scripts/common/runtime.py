"""Hand over to an interpreter that has the skill's one optional dependency.

The digest email needs jinja2. Telling the caller to `pip install jinja2` leaves
them guessing which of several interpreters to install it into — and the answer
is often already sitting in a `.venv` above the skill, or in the venv the
plugin builds for itself. So look for an interpreter that has the module and
re-exec into it instead of failing.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from common.paths import SKILL_ROOT, plugin_data_root

# Set on the child so a re-exec can never loop, even if the chosen interpreter
# turns out to lack the module after all.
SENTINEL = "RUNNING_COACH_REEXECED"


def _venvs_above_skill():
    """Any .venv / venv sitting above the skill, nearest first.

    Replaces an earlier check for a marker file that identified the source
    repo. Looking for the venv directly is both simpler and broader: it finds
    one wherever the skill has been dropped, not only in the repo.
    """
    for parent in SKILL_ROOT.parents:
        for name in (".venv", "venv"):
            if (parent / name).is_dir():
                yield parent / name


def _candidates():
    """Interpreters worth trying, most specific first.

    Compared as literal paths, never resolved: a venv's bin/python is a symlink
    to the base interpreter, so resolving would collapse the one interpreter
    that has the package into the one that doesn't.
    """
    seen, out = {Path(sys.executable)}, []
    roots = []
    if os.environ.get("VIRTUAL_ENV"):
        roots.append(Path(os.environ["VIRTUAL_ENV"]))
    plugin_data = plugin_data_root()
    if plugin_data:
        roots.append(plugin_data / "venv")
    roots += _venvs_above_skill()
    for base in roots:
        for name in ("python3", "python"):
            candidate = base / "bin" / name
            if candidate.exists() and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def _has_module(interpreter, module):
    try:
        return subprocess.run(
            [str(interpreter), "-c", f"import {module}"],
            capture_output=True, timeout=30,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_dependency(module, purpose):
    """Return once `module` is importable, re-execing or exiting 1 if it isn't."""
    if importlib.util.find_spec(module):
        return
    if not os.environ.get(SENTINEL):
        for interpreter in _candidates():
            if _has_module(interpreter, module):
                print(f"note: {module} is missing from {sys.executable}; "
                      f"re-running with {interpreter}", file=sys.stderr)
                os.environ[SENTINEL] = "1"
                os.execv(str(interpreter), [str(interpreter), *sys.argv])
    # Only name places this process would actually go on to look, so the advice
    # cannot send someone to build a venv that would then be ignored.
    reqs = SKILL_ROOT / "requirements.txt"
    options = [f"install it for this interpreter: {sys.executable} -m pip "
               f"install -r {reqs}",
               f"or build a venv beside the skill, which is found "
               f"automatically: cd {SKILL_ROOT.parent} && python3 -m venv "
               f".venv && .venv/bin/pip install -r {reqs}",
               "or activate any virtualenv that has it; $VIRTUAL_ENV is "
               "checked too"]
    print(f"error: {module} is not installed, and it is needed to {purpose}.\n"
          + "\n".join(f"  {o}" for o in options), file=sys.stderr)
    sys.exit(1)

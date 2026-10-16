"""The mount shim: what the `ak` CLI loads for `ak observer …`.

plugin.json declares `cli: {command: observer, entry: scripts/cli.py, attr: observer}`;
the `ak` CLI's `_mount_plugin_clis` loads this file BY PATH, inside its own venv, so
the package under src/ is put on the path here rather than expected to be installed.
The verbs import rich-click, which the `ak` CLI carries.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observer.cli import brain_search, brain_status, observer  # noqa: E402,F401

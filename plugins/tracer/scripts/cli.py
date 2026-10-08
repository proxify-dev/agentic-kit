"""The mount shim: what the `ak` CLI loads for `ak sessions …`, `ak trace …` and `ak tracer …`.

plugin.json's `cli:` list mounts three objects from this one file: `sessions` and `trace`
(the two homes), and `cli` hidden as `ak tracer` (every old spelling). The ak's
`_mount_plugin_clis` loads this file BY PATH, inside its own venv, so the package under
src/ is put on the path here rather than expected to be installed. Everything the verbs
import is stdlib + click (which the `ak` CLI carries).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tracer.cli import brain_search, brain_status, cli  # noqa: E402,F401
from tracer.cli import sessions_group as sessions  # noqa: E402,F401
from tracer.cli import trace_group as trace  # noqa: E402,F401

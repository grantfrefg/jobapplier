"""Enable `python -m applypilot`."""

import os
import sys

# When launched by the web UI, write all output to the log file it tails.
# (A windowed desktop app has no usable console for the child to inherit.)
_child_log = os.environ.get("APPLYPILOT_CHILD_LOG")
if _child_log:
    sys.stdout = sys.stderr = open(_child_log, "a", encoding="utf-8", buffering=1)

from applypilot.cli import app  # noqa: E402

app()

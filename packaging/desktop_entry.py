"""Entry point for the packaged ApplyPilot desktop app.

Double-click (no arguments) -> open the web UI (applypilot.desktop).
With arguments               -> behave like the `applypilot` CLI. The web UI
                                uses this to run pipeline stages in a child
                                process, since a packaged app has no `python -m`.
"""

import multiprocessing
import runpy
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:
        runpy.run_module("applypilot", run_name="__main__")
    else:
        from applypilot.desktop import main

        main()

"""Smoke-test a packaged ApplyPilot build (used by the desktop workflow).

Usage: python packaging/smoke_test.py <path to ApplyPilot executable>
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# The app's output contains non-ASCII (→, ✓); Windows CI consoles default to cp1252
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

exe = Path(sys.argv[1]).resolve()
data = Path(tempfile.mkdtemp(prefix="applypilot-smoke-"))
env = dict(os.environ, APPLYPILOT_DIR=str(data), APPLYPILOT_NO_BROWSER="1")


def call(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:8000{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# 1. CLI mode, exactly as the UI runs pipeline stages (output via APPLYPILOT_CHILD_LOG)
log = data / "cli.log"
subprocess.run([str(exe), "doctor"], env=dict(env, APPLYPILOT_CHILD_LOG=str(log)), timeout=120, check=True)
out = log.read_text(encoding="utf-8")
print(out)
assert "ApplyPilot Doctor" in out, "CLI mode produced no doctor output"

# 2. Desktop mode: server comes up in friend mode
app = subprocess.Popen([str(exe)], env=env)
try:
    for _ in range(120):
        try:
            status = call("/api/status")
            break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit("desktop app never started serving")
    assert status["app"] == "applypilot" and status["desktop"], status
    assert status["engine"] == "local_playwright", status
    print("browser check:", call("/api/browser"))

    # 3. A pipeline stage through the packaged child process
    call("/api/pipeline", {"stages": ["pdf"]})
    for _ in range(120):
        task = call("/api/pipeline")
        if not task["running"]:
            break
        time.sleep(1)
    print("\n".join(task["log"]))
    assert task["returncode"] == 0, task

    # 4. Quit button
    call("/api/shutdown", {})
    app.wait(timeout=30)
    print("OK: packaged app passed smoke test")
finally:
    if app.poll() is None:
        app.kill()

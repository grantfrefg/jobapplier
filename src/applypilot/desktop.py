"""Double-click desktop launcher: starts the web UI and opens it in the browser.

This is what the packaged Windows .exe / macOS .app runs (friend mode):

  * only one copy runs at a time; opening it again just reopens the page
  * the free local engine is forced on and dev-only settings are hidden
  * Playwright's Chromium (if needed) installs under ~/.applypilot/browsers,
    never inside the app bundle
  * the app exits when you press Quit in the page, or after it has been
    idle (no page open, nothing running) for a while
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

PORTS = range(8000, 8011)
IDLE_EXIT_SECONDS = 30 * 60


def _existing_instance(port: int) -> dict | None:
    """The status of a Jobapplier already running on this port, or None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            status = json.load(r)
    except Exception:
        return None
    return status if isinstance(status, dict) and status.get("app") == "applypilot" else None


def _replace_old_instance(port: int, running: str, ours: str) -> bool:
    """Ask an older (or newer) copy that's still running to quit, so this version serves the page.

    Updating the app while the old one still runs (an open tab keeps it alive) used to reopen the old
    server, which then served the new version's page without its scripts and styles. True once the
    port is free.
    """
    logging.info("Jobapplier %s is still running on port %d; replacing it with %s", running or "(older)", port, ours)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "Origin": f"http://localhost:{port}",
                                          "Host": f"localhost:{port}"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as e:
        logging.warning("Couldn't ask the running copy to quit: %s", e)
        return False
    for _ in range(50):  # it finishes the request in hand, then exits
        if not _listening(port):  # (a test bind fails for a while after it closes; uvicorn's doesn't)
            return True
        time.sleep(0.2)
    return False


def _listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> None:
    os.environ["APPLYPILOT_DESKTOP"] = "1"
    if getattr(sys, "frozen", False):
        from applypilot import tls
        tls.use_bundled_certificates()  # the bundled Python has no trusted certificates on macOS

    from applypilot import config

    config.ensure_dirs()
    # Keep downloaded browsers out of the (possibly signed / read-only) bundle
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(config.BROWSERS_DIR))

    if getattr(sys, "frozen", False) or sys.stdout is None:
        # Windowed app: no console, so keep a log for troubleshooting
        log = open(config.LOG_DIR / "desktop.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr, force=True)

    from applypilot import __version__
    for port in PORTS:
        running = _existing_instance(port)
        if running and running.get("version") == __version__:
            webbrowser.open(f"http://localhost:{port}")
            return
        if running and _replace_old_instance(port, str(running.get("version") or ""), __version__):
            break
        if running:  # it wouldn't quit (busy applying?): use it rather than run two copies
            webbrowser.open(f"http://localhost:{port}")
            return
        if _port_free(port):
            break
    else:
        raise SystemExit("No free port between 8000 and 8010")

    import uvicorn
    from applypilot.ui.server import create_app

    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def _quit() -> None:
        server.should_exit = True

    app.state.shutdown = _quit
    url = f"http://localhost:{port}"

    def _open_when_ready() -> None:
        if os.environ.get("APPLYPILOT_NO_BROWSER"):  # CI smoke tests
            return
        for _ in range(100):
            if server.started:
                webbrowser.open(url)
                return
            time.sleep(0.1)

    def _idle_watchdog() -> None:
        while not server.should_exit:
            time.sleep(30)
            idle = time.time() - app.state.last_request
            if idle > IDLE_EXIT_SECONDS and not app.state.busy():
                logging.info("Idle for %ds, exiting", idle)
                _quit()

    threading.Thread(target=_open_when_ready, daemon=True).start()
    threading.Thread(target=_idle_watchdog, daemon=True).start()
    logging.info("Jobapplier desktop on %s (data: %s)", url, config.APP_DIR)
    server.run()


if __name__ == "__main__":
    main()

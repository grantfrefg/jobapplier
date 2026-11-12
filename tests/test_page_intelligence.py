"""Smart extract used to read API bodies inside Playwright's response event, which holds up its event
loop while each body arrives (on the live Hays page the read took 42 s; 19 s once bodies are read after
loading). This checks the new way still captures the API data and skips a response that never finishes."""

import http.server
import json
import threading
import time

import pytest

from applypilot import browser
from applypilot.discovery import smartextract as se

PAGE = b"""<html><body><h1>Jobs</h1><script>
fetch('/api/jobs');
fetch('/api/stream');
setInterval(() => fetch('/ping?' + Math.random()), 200);
</script></body></html>"""
JOBS = json.dumps({"results": [{"title": "Solution Engineer", "id": 1}]}).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/api/stream":  # headers now, body never
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b"[")
            self.wfile.flush()
            time.sleep(60)
            return
        body, ct = (JOBS, "application/json") if self.path == "/api/jobs" else (PAGE, "text/html")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_unfinished_response_doesnt_stall_the_page_read(monkeypatch):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:  # skip where no browser can start (CI without a display)
        try:
            browser.launch_chromium(p).close()
        except Exception as e:
            pytest.skip(f"no Chromium: {e}")
    monkeypatch.setattr(browser, "IDLE_WAIT_MS", 1500)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        t0 = time.time()
        intel = se.collect_page_intelligence(f"http://127.0.0.1:{server.server_port}/")
        assert time.time() - t0 < 20
        urls = [r["url"] for r in intel["api_responses"]]
        assert any(u.endswith("/api/jobs") for u in urls)
        assert not any(u.endswith("/api/stream") for u in urls)
        assert "Jobs" in intel["full_html"]
    finally:
        server.shutdown()

"""Busy pages never go network-idle; waiting for it used Playwright's default 30 s and then failed with
"Timeout 30000ms exceeded". wait_idle gives up quietly instead, and PDFs don't wait on it at all."""

import http.server
import threading

import pytest

from applypilot import browser

BUSY = b"""<html><body>busy<script>setInterval(() => fetch('/ping?' + Math.random()), 100)</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = BUSY if self.path == "/" else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_a_page_that_never_goes_quiet_doesnt_fail():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            try:
                b = browser.launch_chromium(p)
            except Exception as e:  # no browser installed here
                pytest.skip(f"no Chromium: {e}")
            page = b.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            assert browser.wait_idle(page, 1500) is False  # gave up, no exception
            quiet = b.new_page()
            quiet.goto(f"http://127.0.0.1:{server.server_port}/quiet")
            assert browser.wait_idle(quiet, 5000) is True
            b.close()
    finally:
        server.shutdown()

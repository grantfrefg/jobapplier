"""The normal (non-automated) login window on the engine's browser profile."""

import subprocess
import sys

import pytest

from applypilot import config
from applypilot.apply import login_browser


def test_login_command_uses_the_engine_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_BROWSER_PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setenv("CHROME_PATH", sys.executable)  # any existing file stands in for the browser
    cmd = login_browser.login_command()
    assert cmd[0] == sys.executable
    assert f"--user-data-dir={tmp_path / 'profile'}" in cmd
    assert cmd[-3:] == login_browser.START_PAGES
    assert not any("automation" in a or "remote-debugging" in a for a in cmd)  # a normal browser
    assert login_browser.login_command(["https://acme.wd3.myworkdayjobs.com/x"])[-1] == \
        "https://acme.wd3.myworkdayjobs.com/x"


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX sleep stand-in")
def test_ui_login_window_blocks_the_engine_until_closed(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from applypilot import database
    from applypilot.ui import server
    for name, value in {"APP_DIR": tmp_path, "ENV_PATH": tmp_path / ".env", "PROFILE_PATH": tmp_path / "p.json",
                        "LOG_DIR": tmp_path / "logs", "DB_PATH": tmp_path / "t.db"}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "load_env", lambda: None)
    opened = []

    def fake_open(urls=None):
        opened.append(urls)
        return subprocess.Popen(["sleep", "30"])

    monkeypatch.setattr(login_browser, "open_login_browser", fake_open)
    with TestClient(server.create_app()) as client:
        assert client.post("/api/browser/login", json={"url": "https://acme.myworkdayjobs.com/x"}).json()["login_open"]
        assert opened == [["https://acme.myworkdayjobs.com/x"]]
        r = client.post("/api/browser/test")
        assert r.status_code == 409 and "login window" in r.json()["detail"]
        assert client.post("/api/browser/login/close").json()["login_open"] is False
    database.close_connection(tmp_path / "t.db")

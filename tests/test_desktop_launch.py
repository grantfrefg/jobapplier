"""The desktop launcher replaces a running copy of another version (an update while the old app still
ran used to reopen the old server, which served the new page without its scripts and styles)."""

import os

import pytest

from applypilot import __version__, desktop


class Stop(Exception):
    pass


@pytest.fixture
def launch(monkeypatch, tmp_path):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    # main() sets these for the app's own process; put them back afterwards, or every later browser test
    # looks for Chromium in this test's folder (it did on CI)
    for name in ("APPLYPILOT_DESKTOP", "PLAYWRIGHT_BROWSERS_PATH"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:  # set then remove, so pytest remembers it was absent and removes main()'s value afterwards
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
    opened, replaced = [], []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)

    def start(**kw):
        def fake_create_app():
            raise Stop  # got as far as starting this version's own server
        monkeypatch.setattr("applypilot.ui.server.create_app", fake_create_app)
        monkeypatch.setattr(desktop, "_existing_instance", lambda port: kw.get("running") if port == 8000 else None)
        monkeypatch.setattr(desktop, "_port_free", lambda port: port != 8000)
        monkeypatch.setattr(desktop, "_replace_old_instance",
                            lambda port, running, ours: replaced.append((port, running)) or kw.get("quits", True))
        try:
            desktop.main()
            return "reused"
        except Stop:
            return "started"
    return start, opened, replaced


def test_the_same_version_is_reused(launch):
    start, opened, replaced = launch
    assert start(running={"app": "applypilot", "version": __version__}) == "reused"
    assert opened == ["http://localhost:8000"] and replaced == []


@pytest.mark.parametrize("running", [{"app": "applypilot"}, {"app": "applypilot", "version": "0.12.0"}])
def test_another_version_is_replaced(launch, running):
    start, opened, replaced = launch
    assert start(running=running) == "started"
    assert replaced == [(8000, running.get("version", ""))]


def test_a_copy_that_wont_quit_is_used_rather_than_running_two(launch):
    start, opened, replaced = launch
    assert start(running={"app": "applypilot", "version": "0.12.0"}, quits=False) == "reused"
    assert opened == ["http://localhost:8000"]


def test_the_launcher_tests_leave_the_environment_as_it_was(launch, monkeypatch):
    before = {k: os.environ.get(k) for k in ("APPLYPILOT_DESKTOP", "PLAYWRIGHT_BROWSERS_PATH")}
    start, _, _ = launch
    start(running={"app": "applypilot", "version": __version__})
    monkeypatch.undo()  # what pytest does after each test
    assert {k: os.environ.get(k) for k in before} == before

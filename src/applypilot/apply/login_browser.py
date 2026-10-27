"""A normal browser window on the local engine's profile, for logging in.

Google (and some other sites) refuse sign-ins in a browser that's being
controlled by automation, and the local engine's browser says openly that
it is. This opens the very same browser and profile as an ordinary window,
with no automation attached, so you can log in to Google, LinkedIn, SEEK or
an employer's site yourself. Close it and the engine's browser reuses those
logins (cookies live in the profile). Close the window yourself: the
browser writes cookies to disk when its window closes (and about every
30 seconds), so ending the process early can lose a fresh login.

Only one of the two can use the profile at a time, so the web UI never runs
them together.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from applypilot import config

START_PAGES = [
    "https://accounts.google.com/",
    "https://www.linkedin.com/login",
    "https://www.seek.com.au/",
]


def browser_executable() -> str:
    """The browser the local engine uses (same order as local_engine.launch_browser).

    It has to be the same program: a profile's saved logins are only
    readable by the browser that wrote them.
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        if path and Path(path).exists():
            return path
    except Exception:
        pass
    system = config.find_system_browsers()
    if system:
        return system[0]
    raise RuntimeError("No browser found. Use 'Install browser' on the Setup page, or install Google Chrome.")


def login_command(urls: list[str] | None = None) -> list[str]:
    profile = config.LOCAL_BROWSER_PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    args = [browser_executable(), f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--new-window"]
    # Store saved logins the way the engine's browser does (Playwright's
    # defaults), or it can't read them back.
    if sys.platform.startswith("linux"):
        args.append("--password-store=basic")
        # Containers (the dev container) have no usable Chromium sandbox; the
        # engine's browser runs without it too (Playwright's default).
        args.append("--no-sandbox")
    elif sys.platform == "darwin":
        args.append("--use-mock-keychain")
    return args + list(urls or START_PAGES)


def open_login_browser(urls: list[str] | None = None) -> subprocess.Popen:
    """Start the login window (returns at once; the window stays until the user closes it)."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(login_command(urls), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)

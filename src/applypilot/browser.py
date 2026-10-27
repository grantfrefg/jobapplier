"""Starting a Chromium for background work (PDFs, job discovery, job details).

Playwright's default headless build ("headless shell") is a separate
download that the app's "Install browser" button skips (--no-shell), and
plain `pip install playwright && playwright install --no-shell` setups don't
have it either. So try it, then Playwright's full Chromium in headless mode,
then an installed Chrome / Edge.
"""

from __future__ import annotations


def launch_chromium(p, headless: bool = True, **options):
    """`p.chromium.launch(...)` from whatever browser is installed.

    `options` are passed through (e.g. proxy). Raises RuntimeError listing
    what was tried when nothing starts.
    """
    from applypilot import config

    attempts = [{}, {"channel": "chromium"}] + [{"executable_path": exe} for exe in config.find_system_browsers()]
    errors = []
    for extra in attempts:
        try:
            return p.chromium.launch(headless=headless, **options, **extra)
        except Exception as e:
            errors.append(str(e).strip().splitlines()[0][:120])
    raise RuntimeError("No browser could be started: " + " | ".join(errors))

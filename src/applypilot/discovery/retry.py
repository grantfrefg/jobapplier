"""Searches that failed in the last discovery run, so they can be retried on their own.

A job board that blocks the app (HTTP 429/403) or errors is skipped for the
rest of a run. Each source records what it couldn't search here
(~/.applypilot/discover_failed.json) and `applypilot rerun discover` runs
just those again, instead of every search.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from applypilot import config

SOURCES = ("jobspy", "smartextract")


def _path() -> Path:
    return config.APP_DIR / "discover_failed.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(source: str, failed: list[dict]) -> None:
    """Replace what `source` couldn't search (an empty list clears it)."""
    data = _read()
    if failed:
        data[source] = {"at": datetime.now(timezone.utc).isoformat(), "failed": failed}
    else:
        data.pop(source, None)
    if data:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        _path().unlink(missing_ok=True)


def load(source: str) -> list[dict]:
    return list((_read().get(source) or {}).get("failed") or [])


def summary() -> dict:
    """{"searches": n, "boards": [...]} for the web UI."""
    boards: set[str] = set()
    count = 0
    for item in load("jobspy"):
        count += 1
        boards.update(item.get("sites") or [])
    for item in load("smartextract"):
        count += 1
        boards.add(str(item.get("name", "")))
    return {"searches": count, "boards": sorted(b for b in boards if b)}

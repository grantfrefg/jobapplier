"""Daily Gemini request budget.

Google's free tier limits requests per model per day, resetting at midnight
Pacific time. Every Gemini request ApplyPilot makes is counted here (in the
`llm_usage` table) so a run can stop cleanly when the day's allowance is
used up, instead of failing partway through a stage.

The pipeline (scoring, tailoring, Jora) leaves a reserve untouched so the
apply step still has requests left for the day: GEMINI_DAILY_LIMIT (default
500) and GEMINI_APPLY_RESERVE (default 50) in .env. Only Gemini is budgeted;
OpenAI and local models are not.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

DEFAULT_DAILY_LIMIT = 500
DEFAULT_APPLY_RESERVE = 50

PIPELINE = "pipeline"  # scoring, tailoring, cover letters, smart extract
APPLY = "apply"        # the local apply engine


class BudgetExhausted(RuntimeError):
    """The day's Gemini requests are used up (for this kind of work)."""


def _pacific_now(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # no time zone database (e.g. Windows without tzdata)
        # US daylight time: second Sunday of March to first Sunday of November
        year = now.year
        march = datetime(year, 3, 8, 10, tzinfo=timezone.utc)
        nov = datetime(year, 11, 1, 9, tzinfo=timezone.utc)
        dst_start = march + timedelta(days=(6 - march.weekday()) % 7)
        dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)
        offset = -7 if dst_start <= now < dst_end else -8
        return now.astimezone(timezone(timedelta(hours=offset)))


def quota_day(now: datetime | None = None) -> str:
    """Today's date in Google's quota time zone (US Pacific)."""
    return _pacific_now(now).date().isoformat()


def hours_until_reset(now: datetime | None = None) -> float:
    local = _pacific_now(now)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return round((midnight - local).total_seconds() / 3600, 1)


def is_budgeted(model: str | None) -> bool:
    return bool(model) and "gemini" in model.lower()


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def daily_limit() -> int:
    return _int_env("GEMINI_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)


def apply_reserve() -> int:
    return min(_int_env("GEMINI_APPLY_RESERVE", DEFAULT_APPLY_RESERVE), daily_limit())


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            day      TEXT NOT NULL,
            model    TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, model)
        )
    """)
    conn.commit()


def _conn():
    from applypilot.database import get_connection
    conn = get_connection()
    ensure_table(conn)
    return conn


def used(model: str) -> int:
    row = _conn().execute("SELECT requests FROM llm_usage WHERE day = ? AND model = ?",
                          (quota_day(), model)).fetchone()
    return row[0] if row else 0


def remaining(model: str, purpose: str = PIPELINE) -> int:
    """Requests this kind of work may still make today."""
    left = daily_limit() - used(model)
    if purpose == PIPELINE:
        left -= apply_reserve()
    return max(0, left)


def record(model: str, n: int = 1) -> None:
    """Count requests Google accepted."""
    if not is_budgeted(model):
        return
    conn = _conn()
    conn.execute("INSERT INTO llm_usage (day, model, requests) VALUES (?, ?, ?) "
                 "ON CONFLICT(day, model) DO UPDATE SET requests = requests + excluded.requests",
                 (quota_day(), model, n))
    conn.commit()


def mark_exhausted(model: str) -> None:
    """Google says today's quota is gone: believe it over our count."""
    if not is_budgeted(model):
        return
    conn = _conn()
    conn.execute("INSERT INTO llm_usage (day, model, requests) VALUES (?, ?, ?) "
                 "ON CONFLICT(day, model) DO UPDATE SET requests = MAX(requests, excluded.requests)",
                 (quota_day(), model, daily_limit()))
    conn.commit()


def _message(model: str, purpose: str) -> str:
    when = f"Google resets it in about {hours_until_reset():g} hours (midnight US Pacific time)"
    if purpose == PIPELINE and daily_limit() - used(model) > 0:
        return (f"Daily Gemini budget for finding and preparing jobs is used up ({used(model)}/"
                f"{daily_limit()} requests today, {apply_reserve()} kept for applying). {when}.")
    return f"Daily Gemini limit reached ({daily_limit()} requests for {model}). {when}."


def check(model: str | None, purpose: str = PIPELINE) -> None:
    """Raise BudgetExhausted if this kind of work has no requests left today."""
    if is_budgeted(model) and remaining(model, purpose) <= 0:
        raise BudgetExhausted(_message(model, purpose))


def exhausted_error(model: str) -> BudgetExhausted:
    """Record that Google refused for quota, and the error to raise for it."""
    mark_exhausted(model)
    return BudgetExhausted(_message(model, APPLY))


def is_daily_quota_error(status_code: int, body: str) -> bool:
    """A 429 for the per-day quota (retrying today won't help), not per-minute."""
    text = (body or "").lower()
    return status_code == 429 and ("perday" in text or "per day" in text or "daily" in text)


def status(model: str | None) -> dict | None:
    """Today's usage for the UI, or None when the model isn't budgeted."""
    if not is_budgeted(model):
        return None
    n = used(model)
    return {"model": model, "day": quota_day(), "used": n, "limit": daily_limit(),
            "left": max(0, daily_limit() - n), "apply_reserve": apply_reserve(),
            "pipeline_left": remaining(model, PIPELINE), "resets_in_hours": hours_until_reset()}

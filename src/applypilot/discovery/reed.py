"""Reed.co.uk jobs through Reed's official API (UK searches only).

Each user pastes their own free key from https://www.reed.co.uk/developers/jobseeker
(Setup -> What jobs to look for -> Reed API key, saved as REED_API_KEY). Without a key,
Reed is skipped. The search returns a short description; each result's full description
comes from the job's details call. Reed also says how many people have applied.
"""

from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

import httpx

from applypilot import config, market

log = logging.getLogger(__name__)

API = "https://www.reed.co.uk/api/1.0"
# Search settings job_type -> Reed's filters (contract also takes temp roles)
JOB_TYPES = {"fulltime": [{"fullTime": "true"}], "parttime": [{"partTime": "true"}],
             "contract": [{"contract": "true"}, {"temp": "true"}]}
MAX_RESULTS = 100  # per search; Reed's own maximum page size


def api_key() -> str:
    return os.environ.get("REED_API_KEY", "").strip()


def _get(client: httpx.Client, path: str, params: dict | None = None) -> dict:
    response = client.get(f"{API}{path}", params=params, auth=(api_key(), ""), timeout=30)
    if response.status_code == 401:
        raise PermissionError("Reed rejected the API key (Setup -> What jobs to look for -> Reed API key)")
    response.raise_for_status()
    return response.json()


def _text(value: str | None) -> str:
    """Plain text from Reed's HTML descriptions."""
    text = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", value or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def _salary(job: dict) -> str | None:
    low, high = job.get("minimumSalary"), job.get("maximumSalary")
    if not low and not high:
        return None
    fmt = lambda v: f"£{int(v):,}"  # noqa: E731
    return f"{fmt(low)}-{fmt(high)}" if low and high and low != high else fmt(low or high)


def search(client: httpx.Client, query: str, location: str, job_type: str = "") -> list[dict]:
    """Reed results for one search: [{url, title, company, location, salary, description, applications}]."""
    params = {"keywords": query, "locationName": location.split(",")[0].strip(), "distanceFromLocation": 15,
              "resultsToTake": MAX_RESULTS}
    seen, jobs = set(), []
    for extra in JOB_TYPES.get(job_type or "", [{}]):
        for job in (_get(client, "/search", {**params, **extra}).get("results") or []):
            if job.get("jobId") in seen:
                continue
            seen.add(job.get("jobId"))
            jobs.append(job)
    return jobs


def details(client: httpx.Client, job_id) -> dict:
    try:
        return _get(client, f"/jobs/{job_id}")
    except Exception as e:  # the short description from the search still works
        log.debug("Reed details for %s: %s", job_id, e)
        return {}


def board_job_type(job: dict) -> str | None:
    """Reed's contractType (Permanent, Contract, Temporary) and full/part time as e.g. "fulltime,contract"."""
    names = [str(job.get("contractType") or "").strip().lower()]
    names += [name for key, name in (("fullTime", "fulltime"), ("partTime", "parttime")) if job.get(key) is True]
    return ",".join(n for n in names if n) or None


def store(conn: sqlite3.Connection, results: list[dict], client: httpx.Client | None = None) -> tuple[int, int]:
    """Store Reed jobs (full descriptions fetched for new ones). Returns (new, existing)."""
    from applypilot import applicants
    now = datetime.now(timezone.utc).isoformat()
    new = existing = 0
    for job in results:
        url = job.get("jobUrl") or (f"https://www.reed.co.uk/jobs/{job.get('jobId')}" if job.get("jobId") else "")
        if not url:
            continue
        count = job.get("applications")
        shown = applicants.parse(f"{count} applicants") if isinstance(count, int) else None
        if conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone():
            existing += 1
            if shown:
                conn.execute("UPDATE jobs SET applicants = ?, applicants_count = ? WHERE url = ?", (*shown, url))
            continue
        full = details(client, job["jobId"]) if client and job.get("jobId") else {}
        description = _text(full.get("jobDescription") or job.get("jobDescription"))
        apply_url = full.get("externalUrl") or None
        conn.execute(
            "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, discovered_at, "
            "full_description, application_url, detail_scraped_at, applicants, applicants_count, board_job_type) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Reed', 'reed_api', ?, ?, ?, ?, ?, ?, ?)",
            (url, job.get("jobTitle"), job.get("employerName"), _salary(job), description[:500],
             job.get("locationName"), now, description if len(description) > 200 else None, apply_url,
             now if len(description) > 200 else None, *(shown or (None, None)), board_job_type({**job, **full})))
        new += 1
    conn.commit()
    return new, existing


def run_reed_discovery(cfg: dict | None = None) -> dict:
    """Search Reed for every query in the search settings (UK searches with a key only)."""
    from applypilot.database import get_connection
    cfg = cfg if cfg is not None else config.load_search_config()
    if market.from_country((cfg or {}).get("country")) is not market.UK:
        return {"skipped": "not a UK search"}
    if not api_key():
        log.info("Reed skipped: add a free Reed API key in Setup to search Reed too")
        return {"skipped": "no Reed API key"}
    accept, reject = config.load_location_filter(cfg)
    location = str((cfg.get("defaults") or {}).get("location") or "London")
    job_type = str((cfg.get("defaults") or {}).get("job_type") or "")
    conn = get_connection()
    totals = {"new": 0, "existing": 0, "errors": 0}
    with httpx.Client() as client:
        for q in cfg.get("queries") or []:
            query = q.get("query") if isinstance(q, dict) else str(q)
            if not query:
                continue
            try:
                found = [j for j in search(client, query, location, job_type)
                         if config.location_ok(j.get("locationName"), accept, reject)]
                new, existing = store(conn, found, client)
                totals["new"] += new
                totals["existing"] += existing
                log.info("Reed: \"%s\" in %s: %d new, %d already seen", query, location, new, existing)
            except PermissionError as e:
                log.error("%s", e)
                totals["errors"] += 1
                break
            except Exception as e:
                log.error("Reed search \"%s\" failed: %s", query, e)
                totals["errors"] += 1
    return totals

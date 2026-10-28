"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone

from jobspy import scrape_jobs

from applypilot import config
from applypilot.database import get_connection, init_db, store_jobs

log = logging.getLogger(__name__)


# -- Proxy parsing -----------------------------------------------------------

def parse_proxy(proxy_str: str) -> dict:
    """Parse host:port:user:pass into components."""
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        raise ValueError(
            f"Proxy format not recognized: {proxy_str}. "
            f"Expected: host:port:user:pass or host:port"
        )


# -- Retry wrapper -----------------------------------------------------------

def _scrape_with_retry(kwargs: dict, max_retries: int = 2, backoff: float = 5.0):
    """Call scrape_jobs with retry on transient failures."""
    for attempt in range(max_retries + 1):
        try:
            return scrape_jobs(**kwargs)
        except Exception as e:
            err = str(e).lower()
            transient = any(k in err for k in ("timeout", "429", "proxy", "connection", "reset", "refused"))
            if transient and attempt < max_retries:
                wait = backoff * (attempt + 1)
                log.warning("Retry %d/%d in %.0fs: %s", attempt + 1, max_retries, wait, e)
                time.sleep(wait)
            else:
                raise


# -- Per-site errors and blocks ------------------------------------------------

_SITE_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor",
               "zip_recruiter": "ZipRecruiter", "google": "Google Jobs"}
# A job board refusing us (too many requests, or a block page): asking again
# this run only wastes time and can make the block last longer.
_BLOCKED_RE = re.compile(r"\b(429|403)\b|blocked", re.IGNORECASE)


def site_name(site: str) -> str:
    return _SITE_NAMES.get(site, site.replace("_", " ").title())


class _SiteErrors(logging.Handler):
    """Collects JobSpy's per-site errors (its loggers are "jobspy.<site>") instead of
    printing them unnamed, e.g. "response status code 429 (blocked: use a proxy)"."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.errors: list[tuple[str, str, int]] = []

    def emit(self, record: logging.LogRecord) -> None:
        site = record.name.split(".")[1] if record.name.count(".") >= 1 else "jobspy"
        self.errors.append((site, record.getMessage(), record.levelno))

    def take(self) -> list[tuple[str, str, int]]:
        errors, self.errors = self.errors, []
        return errors


def _describe_error(message: str) -> str:
    if re.search(r"\b429\b", message):
        return "blocked: too many requests (HTTP 429)"
    if re.search(r"\b403\b", message):
        return "blocked: access refused (HTTP 403)"
    return message


# -- Location filtering ------------------------------------------------------

def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str]]:
    """Extract accept/reject location lists from search config."""
    return config.load_location_filter(search_cfg)


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter (see config.location_ok)."""
    return config.location_ok(location, accept, reject)


# -- DB storage (JobSpy DataFrame -> SQLite) ---------------------------------

def store_jobspy_results(conn: sqlite3.Connection, df, source_label: str) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for _, row in df.iterrows():
        url = str(row.get("job_url", ""))
        if not url or url == "nan":
            continue

        title = str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None
        company = str(row.get("company", "")) if str(row.get("company", "")) != "nan" else None
        location_str = str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None

        # Build salary string from min/max
        salary = None
        min_amt = row.get("min_amount")
        max_amt = row.get("max_amount")
        interval = str(row.get("interval", "")) if str(row.get("interval", "")) != "nan" else ""
        currency = str(row.get("currency", "")) if str(row.get("currency", "")) != "nan" else ""
        if min_amt and str(min_amt) != "nan":
            if max_amt and str(max_amt) != "nan":
                salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
            else:
                salary = f"{currency}{int(float(min_amt)):,}"
            if interval:
                salary += f"/{interval}"

        description = str(row.get("description", "")) if str(row.get("description", "")) != "nan" else None
        site_name = str(row.get("site", source_label))
        is_remote = row.get("is_remote", False)

        site_label = f"{site_name}"
        if is_remote:
            location_str = f"{location_str} (Remote)" if location_str else "Remote"

        strategy = "jobspy"

        # If JobSpy gave us a full description, promote it directly
        full_description = None
        detail_scraped_at = None
        if description and len(description) > 200:
            full_description = description
            detail_scraped_at = now

        # Extract apply URL if JobSpy provided it
        # Boards often give no direct link: JobSpy then has None or NaN, and
        # str() of those is "None"/"nan" -- only keep real URLs.
        apply_url = str(row.get("job_url_direct") or "")
        if not apply_url.startswith(("http://", "https://")):
            apply_url = None

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, "
                "discovered_at, full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, title, company, salary, description, location_str, site_label, strategy, now,
                 full_description, apply_url, detail_scraped_at),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


# -- Single search execution -------------------------------------------------

def _run_one_search(
    search: dict,
    sites: list[str],
    results_per_site: int,
    hours_old: int,
    proxy_config: dict | None,
    defaults: dict,
    max_retries: int,
    accept_locs: list[str],
    reject_locs: list[str],
    glassdoor_map: dict,
) -> dict:
    """Run a single search query and store results in DB."""
    s = search
    label = f"\"{s['query']}\" in {s['location']} {'(remote)' if s.get('remote') else ''}"
    if "tier" in s:
        label += f" [tier {s['tier']}]"

    # Split sites: Glassdoor needs simplified location, others use original
    gd_location = glassdoor_map.get(s["location"], s["location"].split(",")[0])
    has_glassdoor = "glassdoor" in sites
    other_sites = [si for si in sites if si != "glassdoor"]

    all_dfs = []
    failed_sites: list[str] = []  # boards whose request raised (others are reported through logging)

    # Run non-Glassdoor sites with original location
    if other_sites:
        kwargs = {
            "site_name": other_sites,
            "search_term": s["query"],
            "location": s["location"],
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "country_indeed": defaults.get("country_indeed", "usa"),
            "verbose": 0,
        }
        if s.get("remote"):
            kwargs["is_remote"] = True
        if proxy_config:
            kwargs["proxies"] = [proxy_config["jobspy"]]
        if "linkedin" in other_sites:
            kwargs["linkedin_fetch_description"] = True
        try:
            df = _scrape_with_retry(kwargs, max_retries=max_retries)
            all_dfs.append(df)
        except Exception as e:
            log.error("[%s] (non-gd): %s", label, e)
            failed_sites += other_sites

    # Run Glassdoor separately with simplified location
    if has_glassdoor:
        gd_kwargs = {
            "site_name": ["glassdoor"],
            "search_term": s["query"],
            "location": gd_location,
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "verbose": 0,
        }
        if s.get("remote"):
            gd_kwargs["is_remote"] = True
        if proxy_config:
            gd_kwargs["proxies"] = [proxy_config["jobspy"]]
        try:
            gd_df = _scrape_with_retry(gd_kwargs, max_retries=max_retries)
            all_dfs.append(gd_df)
        except Exception as e:
            log.error("[%s] (glassdoor): %s", label, e)
            failed_sites.append("glassdoor")

    if not all_dfs:
        log.error("[%s]: all sites failed", label)
        return {"new": 0, "existing": 0, "errors": 1, "filtered": 0, "total": 0, "label": label,
                "failed_sites": failed_sites}

    import pandas as pd
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        df = pd.concat(all_dfs, ignore_index=True) if len(all_dfs) > 1 else all_dfs[0]

    if len(df) == 0:
        log.info("[%s] 0 results", label)
        return {"new": 0, "existing": 0, "errors": 0, "filtered": 0, "total": 0, "label": label,
                "failed_sites": failed_sites}

    # Filter by location before storing
    before = len(df)
    df = df[df.apply(lambda row: str(row.get("is_remote")).lower() == "true" or _location_ok(
        str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None,
        accept_locs, reject_locs,
    ), axis=1)]
    filtered = before - len(df)

    conn = get_connection()
    new, existing = store_jobspy_results(conn, df, s["query"])

    msg = f"[{label}] {before} results -> {new} new, {existing} dupes"
    if filtered:
        msg += f", {filtered} filtered (location)"
    log.info(msg)

    return {"new": new, "existing": existing, "errors": 0, "filtered": filtered, "total": before, "label": label,
            "failed_sites": failed_sites}


# -- Single query search -----------------------------------------------------

def search_jobs(
    query: str,
    location: str,
    sites: list[str] | None = None,
    remote_only: bool = False,
    results_per_site: int = 50,
    hours_old: int = 72,
    proxy: str | None = None,
    country_indeed: str = "usa",
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Search: \"%s\" in %s | sites=%s | remote=%s", query, location, sites, remote_only)

    kwargs = {
        "site_name": sites,
        "search_term": query,
        "location": location,
        "results_wanted": results_per_site,
        "hours_old": hours_old,
        "description_format": "markdown",
        "country_indeed": country_indeed,
        "verbose": 2,
    }

    if remote_only:
        kwargs["is_remote"] = True

    if proxy_config:
        kwargs["proxies"] = [proxy_config["jobspy"]]

    if "linkedin" in sites:
        kwargs["linkedin_fetch_description"] = True

    try:
        df = scrape_jobs(**kwargs)
    except Exception as e:
        log.error("JobSpy search failed: %s", e)
        return {"error": str(e), "total": 0, "new": 0, "existing": 0}

    total = len(df)
    log.info("JobSpy returned %d results", total)

    if total == 0:
        return {"total": 0, "new": 0, "existing": 0}

    if "site" in df.columns:
        site_counts = df["site"].value_counts()
        for site, count in site_counts.items():
            log.info("  %s: %d", site, count)

    conn = init_db()
    new, existing = store_jobspy_results(conn, df, query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
    log.info("DB total: %d jobs, %d pending detail scrape", db_total, pending)

    return {"total": total, "new": new, "existing": existing}


# -- Full crawl (all queries x all locations) --------------------------------

def _full_crawl(
    search_cfg: dict,
    tiers: list[int] | None = None,
    locations: list[str] | None = None,
    sites: list[str] | None = None,
    results_per_site: int = 100,
    hours_old: int = 72,
    proxy: str | None = None,
    max_retries: int = 2,
    searches: list[dict] | None = None,
) -> dict:
    """Run all search queries from search config across all locations.

    `searches` runs just these instead (each may name its own "sites"),
    e.g. the ones that failed last time (discovery/retry.py). Whatever
    can't be searched this time is recorded for the next retry.
    """
    if sites is None:
        sites = ["indeed", "linkedin", "zip_recruiter"]

    # Build search combinations from config
    queries = search_cfg.get("queries", [])
    locs = search_cfg.get("locations", [])
    defaults = search_cfg.get("defaults", {})
    glassdoor_map = search_cfg.get("glassdoor_location_map", {})
    accept_locs, reject_locs = _load_location_config(search_cfg)

    if tiers:
        queries = [q for q in queries if q.get("tier") in tiers]
    if locations:
        locs = [loc for loc in locs if loc.get("label") in locations]

    retrying = searches is not None
    if searches is None:
        searches = []
        for q in queries:
            for loc in locs:
                searches.append({
                    "query": q["query"],
                    "location": loc["location"],
                    "remote": loc.get("remote", False),
                    "tier": q.get("tier", 0),
                })

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("%s: %d search combinations", "Retrying failed searches" if retrying else "Full crawl", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d",
             ", ".join(sites), results_per_site, hours_old)

    # Ensure DB schema is ready
    init_db()

    total_new = 0
    total_existing = 0
    total_errors = 0
    completed = 0
    blocked: dict[str, str] = {}  # site -> why it was skipped for the rest of this run
    failed: list[dict] = []  # searches (and which boards) to retry later

    # JobSpy logs each site's failure under its own logger without naming the
    # site; collect them here and report them by name instead.
    jobspy_log = logging.getLogger("jobspy")
    collector = _SiteErrors()
    jobspy_log.addHandler(collector)
    propagate = jobspy_log.propagate
    jobspy_log.propagate = False
    failed_all_logged = False
    try:
        for s in searches:
            wanted = list(s.get("sites") or sites)
            active = [site for site in wanted if site not in blocked]
            if not active:
                if all(site in blocked for site in sites) and not failed_all_logged:
                    log.warning("Every job board has blocked this run; stopping early (%d of %d searches done).",
                                completed, len(searches))
                    failed_all_logged = True
                failed.append(_failed_search(s, wanted))
                continue
            result = _run_one_search(
                s, active, results_per_site, hours_old,
                proxy_config, defaults, max_retries,
                accept_locs, reject_locs, glassdoor_map,
            )
            missed = set(result.get("failed_sites") or []) | {site for site in wanted if site in blocked}
            for site, message, level in collector.take():
                if level >= logging.ERROR or _BLOCKED_RE.search(message):  # not a one-job warning
                    missed.add(site)
                if site in blocked:
                    continue
                if _BLOCKED_RE.search(message):
                    blocked[site] = _describe_error(message)
                    log.warning("%s: %s. Skipping %s for the rest of this run.", site_name(site),
                                blocked[site], site_name(site))
                else:
                    log.warning("%s: %s", site_name(site), message)
            missed &= set(wanted)
            if missed:
                failed.append(_failed_search(s, sorted(missed)))
            completed += 1
            total_new += result["new"]
            total_existing += result["existing"]
            total_errors += result["errors"]

            if completed % 5 == 0 or completed == len(searches):
                log.info("Progress: %d/%d queries done (%d new, %d dupes, %d errors)",
                         completed, len(searches), total_new, total_existing, total_errors)
    finally:
        jobspy_log.removeHandler(collector)
        jobspy_log.propagate = propagate

    # Final stats
    conn = get_connection()
    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    log.info("Full crawl complete: %d new | %d dupes | %d errors | %d total in DB",
             total_new, total_existing, total_errors, db_total)
    for site, why in blocked.items():
        log.warning("%s was skipped this run (%s). Blocks like this usually lift within a few hours: "
                    "run Find jobs again later (or set a proxy in .env if it keeps happening).", site_name(site), why)
    from applypilot.discovery import retry
    retry.save("jobspy", failed)
    if failed:
        log.warning("%d search(es) didn't finish on every board. To try just those again later: Find jobs → "
                    "Run a step again → Discover: retry failed searches (or `applypilot rerun discover`).",
                    len(failed))
    elif retrying:
        log.info("Every search that failed last time worked this time.")

    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "db_total": db_total,
        "queries": len(searches),
        "blocked": blocked,
        "failed": len(failed),
    }


def _failed_search(search: dict, sites: list[str]) -> dict:
    return {"query": search["query"], "location": search["location"], "remote": bool(search.get("remote")),
            "tier": search.get("tier", 0), "sites": list(sites)}


# -- Public entry point ------------------------------------------------------

def country_indeed(cfg: dict) -> str:
    """Indeed country site to search: defaults.country_indeed, else `country`, else USA.

    Without this, a search for "Sydney" hits indeed.com (US) and finds nothing.
    """
    value = (cfg.get("defaults") or {}).get("country_indeed") or cfg.get("country") or "usa"
    return str(value).strip().lower()


def run_discovery(cfg: dict | None = None, retry_failed: bool = False) -> dict:
    """Main entry point for JobSpy-based job discovery.

    Loads search queries and locations from the user's search config YAML,
    then runs a full crawl across all configured job boards.

    Args:
        cfg: Override the search configuration dict. If None, loads from
             the user's searches.yaml file.
        retry_failed: Only run the searches (and boards) that failed last time.

    Returns:
        Dict with stats: new, existing, errors, db_total, queries.
    """
    if cfg is None:
        cfg = config.load_search_config()

    if not cfg:
        log.warning("No search configuration found. Run `applypilot init` to create one.")
        return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}

    proxy = cfg.get("proxy")
    # "boards" is the key the example config documents; "sites" is the original one
    sites = cfg.get("sites") or cfg.get("boards")
    cfg = {**cfg, "defaults": {**(cfg.get("defaults") or {}), "country_indeed": country_indeed(cfg)}}
    results_per_site = cfg.get("defaults", {}).get("results_per_site", 100)
    hours_old = cfg.get("defaults", {}).get("hours_old", 72)
    tiers = cfg.get("tiers")
    locations = cfg.get("location_labels")

    searches = None
    if retry_failed:
        from applypilot.discovery import retry
        searches = retry.load("jobspy")
        if not searches:
            log.info("No failed LinkedIn/Indeed searches to retry.")
            return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}

    return _full_crawl(
        searches=searches,
        search_cfg=cfg,
        tiers=tiers,
        locations=locations,
        sites=sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy=proxy,
    )

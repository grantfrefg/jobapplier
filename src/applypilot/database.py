"""ApplyPilot database layer: schema, migrations, stats, and connection helpers.

Single source of truth for the jobs table schema. All columns from every
pipeline stage are created up front so any stage can run independently
without migration ordering issues.
"""

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

from applypilot.config import DB_PATH

# Thread-local connection storage — each thread gets its own connection
# (required for SQLite thread safety with parallel workers)
_local = threading.local()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a thread-local cached SQLite connection with WAL mode enabled.

    Each thread gets its own connection (required for SQLite thread safety).
    Connections are cached and reused within the same thread.

    Args:
        db_path: Override the default DB_PATH. Useful for testing.

    Returns:
        sqlite3.Connection configured with WAL mode and row factory.
    """
    path = str(db_path or DB_PATH)

    if not hasattr(_local, 'connections'):
        _local.connections = {}

    conn = _local.connections.get(path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.ProgrammingError:
            pass

    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _local.connections[path] = conn
    return conn


def close_connection(db_path: Path | str | None = None) -> None:
    """Close the cached connection for the current thread."""
    path = str(db_path or DB_PATH)
    if hasattr(_local, 'connections'):
        conn = _local.connections.pop(path, None)
        if conn is not None:
            conn.close()


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create the full jobs table with all columns from every pipeline stage.

    This is idempotent -- safe to call on every startup. Uses CREATE TABLE IF NOT EXISTS
    so it won't destroy existing data.

    Schema columns by stage:
      - Discovery:  url, title, salary, description, location, site, strategy, discovered_at
      - Enrichment: full_description, application_url, detail_scraped_at, detail_error
      - Scoring:    fit_score, score_reasoning, scored_at
      - Tailoring:  tailored_resume_path, tailored_at, tailor_attempts
      - Cover:      cover_letter_path, cover_letter_at, cover_attempts
      - Apply:      applied_at, apply_status, apply_error, apply_attempts,
                   agent_id, last_attempted_at, apply_duration_ms, apply_task_id,
                   verification_confidence

    Args:
        db_path: Override the default DB_PATH.

    Returns:
        sqlite3.Connection with the schema initialized.
    """
    path = db_path or DB_PATH

    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            -- Discovery stage (smart_extract / job_search)
            url                   TEXT PRIMARY KEY,
            title                 TEXT,
            company               TEXT,
            salary                TEXT,
            description           TEXT,
            location              TEXT,
            site                  TEXT,
            strategy              TEXT,
            discovered_at         TEXT,

            -- Enrichment stage (detail_scraper)
            full_description      TEXT,
            application_url       TEXT,
            detail_scraped_at     TEXT,
            detail_error          TEXT,

            -- Scoring stage (job_scorer)
            fit_score             INTEGER,
            score_reasoning       TEXT,
            scored_at             TEXT,

            -- Tailoring stage (resume tailor)
            tailored_resume_path  TEXT,
            tailored_at           TEXT,
            tailor_attempts       INTEGER DEFAULT 0,

            -- Cover letter stage
            cover_letter_path     TEXT,
            cover_letter_at       TEXT,
            cover_attempts        INTEGER DEFAULT 0,

            -- Application stage
            applied_at            TEXT,
            apply_status          TEXT,
            apply_error           TEXT,
            apply_attempts        INTEGER DEFAULT 0,
            agent_id              TEXT,
            last_attempted_at     TEXT,
            apply_duration_ms     INTEGER,
            apply_task_id         TEXT,
            verification_confidence TEXT,

            -- Cross-site de-duplication: URL of the listing this one repeats
            duplicate_of          TEXT
        )
    """)
    conn.commit()

    # Run migrations for any columns added after initial schema
    ensure_columns(conn)

    from applypilot.apply.answers import ensure_table
    ensure_table(conn)  # the answer bank (screening-question answers)
    from applypilot import budget
    budget.ensure_table(conn)  # daily Gemini request counts

    # Older JobSpy imports stored the text "None" as the application link when a
    # board gave none; treat those as no link. Relative links (Jora's
    # "/job/rd/..." redirect to the employer's site) are made absolute.
    for url, link in conn.execute(
        "SELECT url, application_url FROM jobs "
        "WHERE application_url IS NOT NULL AND application_url NOT LIKE 'http%'"
    ).fetchall():
        conn.execute("UPDATE jobs SET application_url = ? WHERE url = ?", (_absolute_link(url, link), url))
    conn.commit()

    return conn


_AUTH_WALL_RE = re.compile(r"linkedin\.com/(signup|login|uas/login|authwall|checkpoint)"
                           r"|jora\.com/users/(sign_in|sign_up)|seek\.com\.au/oauth|/(login|signin|sign_in|signup|"
                           r"sign_up|register)(?:[/?#]|$)", re.IGNORECASE)


def _absolute_link(page_url: str, link) -> str | None:
    """`link` as an absolute http(s) URL, resolving "/path" against the listing; None if it isn't a link."""
    if not isinstance(link, str):
        return None
    link = link.strip()
    if link.startswith(("http://", "https://")):
        # A board's sign-up / sign-in wall (LinkedIn's "cold-join" when scraped
        # logged out) isn't an application: open the listing instead.
        return None if _AUTH_WALL_RE.search(link) else link
    if link.startswith("//"):
        return "https:" + link  # protocol-relative
    if link.startswith("/") and str(page_url or "").startswith(("http://", "https://")):
        return urljoin(page_url, link)
    return None


def apply_link(job) -> str:
    """The page to open to apply: the application link if it's a real URL, else the listing."""
    link = job["application_url"] if "application_url" in job.keys() else None
    return _absolute_link(job["url"], link) or job["url"]


# Complete column registry: column_name -> SQL type with optional default.
# This is the single source of truth. Adding a column here is all that's needed
# for it to appear in both new databases and migrated ones.
_ALL_COLUMNS: dict[str, str] = {
    # Discovery
    "url": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "salary": "TEXT",
    "description": "TEXT",
    "location": "TEXT",
    "site": "TEXT",
    "strategy": "TEXT",
    "discovered_at": "TEXT",
    # Enrichment
    "full_description": "TEXT",
    "application_url": "TEXT",
    "detail_scraped_at": "TEXT",
    "detail_error": "TEXT",
    # Scoring
    "fit_score": "INTEGER",
    "score_reasoning": "TEXT",
    "scored_at": "TEXT",
    # Tailoring
    "tailored_resume_path": "TEXT",
    "tailored_at": "TEXT",
    "tailor_attempts": "INTEGER DEFAULT 0",
    # Cover letter
    "cover_letter_path": "TEXT",
    "cover_letter_at": "TEXT",
    "cover_attempts": "INTEGER DEFAULT 0",
    # Application
    "applied_at": "TEXT",
    "apply_status": "TEXT",
    "apply_error": "TEXT",
    "apply_attempts": "INTEGER DEFAULT 0",
    "agent_id": "TEXT",
    "last_attempted_at": "TEXT",
    "apply_duration_ms": "INTEGER",
    "apply_task_id": "TEXT",
    "verification_confidence": "TEXT",
    # Cross-site de-duplication
    "company": "TEXT",
    "duplicate_of": "TEXT",
    # Redo: write the resume and cover letter again (old ones kept until then)
    "redo_requested": "INTEGER DEFAULT 0",
    # Where the application really happens (apply/sites.py): key, display
    # name, account needed (no / board / yes / unknown) and the page found
    "apply_site": "TEXT",
    "apply_site_name": "TEXT",
    "apply_account": "TEXT",
    "apply_site_url": "TEXT",
    "apply_site_checked_at": "TEXT",
    # ATS check of the tailored resume (scoring/ats.py): 0-100 and the details
    "ats_score": "INTEGER",
    "ats_report": "TEXT",
}


def ensure_columns(conn: sqlite3.Connection | None = None) -> list[str]:
    """Add any missing columns to the jobs table (forward migration).

    Reads the current table schema via PRAGMA table_info and compares against
    the full column registry. Any missing columns are added with ALTER TABLE.

    This makes it safe to upgrade the database from any previous version --
    columns are only added, never removed or renamed.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        List of column names that were added (empty if schema was already current).
    """
    if conn is None:
        conn = get_connection()

    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    added = []

    for col, dtype in _ALL_COLUMNS.items():
        if col not in existing:
            # PRIMARY KEY columns can't be added via ALTER TABLE, but url
            # is always created with the table itself so this is safe
            if "PRIMARY KEY" in dtype:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {dtype}")
            added.append(col)

    if added:
        conn.commit()

    return added


# ---------------------------------------------------------------------------
# Cross-site de-duplication
# ---------------------------------------------------------------------------
# The same role is often listed on Indeed, LinkedIn and Jora under three
# different URLs. Each row keeps its URL, but every repeat gets
# duplicate_of = <canonical URL>, and pipeline stages and apply skip repeats.

_COMPANY_SUFFIXES = {
    "pty", "ltd", "limited", "inc", "incorporated", "llc", "plc", "corp", "corporation",
    "co", "company", "group", "holdings", "australia", "aust", "au", "the",
}


def _norm_words(text: str | None) -> list[str]:
    text = (text or "").lower().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", text)


def normalize_company(name: str | None) -> str:
    """'Acme Pty Ltd' and 'ACME Group' -> 'acme'."""
    words = _norm_words(name)
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def normalize_title(title: str | None) -> str:
    return " ".join(_norm_words(title))


# Query parameters that only track where a click came from; everything else
# can identify the job (?jobId=123, PageUp's sData, Jora's return_to).
_TRACKING_PARAM = re.compile(r"^(utm\w*|source|src|ref|referrer|trk\w*|gh_src|lever-source|iis|iisn|"
                             r"campaign|medium|fbclid|gclid|mc_\w+|sp|sr|sq|sl|tk|sol_key|trigger_source)$",
                             re.IGNORECASE)


def _norm_apply_url(url: str | None) -> str:
    """An employer apply link, comparable across listings: tracking parameters,
    fragment and trailing slash removed. A board's sign-in or sign-up page isn't
    a job's link (many jobs share it), so it gives "" and never matches."""
    if not url or not url.startswith("http") or _AUTH_WALL_RE.search(url):
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    query = "&".join(sorted(f"{k}={v}" for k, v in parse_qsl(parts.query, keep_blank_values=True)
                            if not _TRACKING_PARAM.match(k)))
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


def _shingles(text: str | None, n: int = 5) -> frozenset:
    words = _norm_words(text)
    return frozenset(tuple(words[i:i + n]) for i in range(max(len(words) - n + 1, 0)))


def _overlap(a: frozenset, b: frozenset) -> float:
    """Share of the shorter description that also appears in the longer one."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


_MIN_SHINGLES = 40  # ~45 words: shorter texts are too generic to compare


def _same_role(a: dict, b: dict) -> bool:
    """a and b already have the same normalized title."""
    if a["company_n"] and b["company_n"] and a["company_n"] == b["company_n"]:
        return True
    sa, sb = a["shingles"], b["shingles"]
    if len(sa) < _MIN_SHINGLES or len(sb) < _MIN_SHINGLES:
        return False
    # Different (or unknown) company names: only near-identical text counts,
    # e.g. a recruiter reposting the employer's ad. Stricter when both names
    # are known and differ.
    threshold = 0.9 if (a["company_n"] and b["company_n"]) else 0.8
    return _overlap(sa, sb) >= threshold


DEFAULT_SITE_PRIORITY = ("linkedin", "indeed", "jora")


def _site_priority() -> list[str]:
    """Preferred listing source for a role (searches.yaml `site_priority`)."""
    from applypilot.config import load_search_config
    try:
        configured = load_search_config().get("site_priority")
    except Exception:
        configured = None
    return [str(s).lower() for s in (configured or DEFAULT_SITE_PRIORITY)]


def mark_duplicates(conn: sqlite3.Connection | None = None) -> int:
    """Link repeat listings of the same role to one canonical row.

    Two listings are the same role when they share an employer apply link,
    or have the same title and the same company, or (company unknown or
    different) the same title and near-identical descriptions.

    The canonical row is the one furthest along (applied, then tailored;
    a listing waiting for Redo doesn't count as tailored),
    then the preferred site (`site_priority`, default LinkedIn > Indeed >
    Jora), then highest fit score, then first discovered. Idempotent;
    returns the number of rows whose duplicate_of changed.
    """
    if conn is None:
        conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT rowid, url, title, company, site, application_url, description, full_description, "
        "applied_at, tailored_resume_path, fit_score, duplicate_of, redo_requested FROM jobs"
    ).fetchall()]
    priority = _site_priority()

    def site_rank(site: str | None) -> int:
        site = (site or "").lower()
        return priority.index(site) if site in priority else len(priority)

    rows.sort(key=lambda r: (r["applied_at"] is None,
                             r["tailored_resume_path"] is None or bool(r["redo_requested"]),
                             site_rank(r["site"]),
                             -(r["fit_score"] if r["fit_score"] is not None else -1), r["rowid"]))

    by_apply_url: dict[str, dict] = {}
    by_title: dict[str, list[dict]] = {}
    changed = 0
    for r in rows:
        r["company_n"] = normalize_company(r["company"])
        r["shingles"] = _shingles(r["full_description"] or r["description"])
        title_n = normalize_title(r["title"])
        apply_key = _norm_apply_url(r["application_url"])

        canon = by_apply_url.get(apply_key) if apply_key else None
        if canon is None and title_n:
            canon = next((c for c in by_title.get(title_n, []) if _same_role(r, c)), None)

        if canon is None:
            if apply_key:
                by_apply_url.setdefault(apply_key, r)
            if title_n:
                by_title.setdefault(title_n, []).append(r)
        target = canon["url"] if canon else None
        if target != r["duplicate_of"]:
            conn.execute("UPDATE jobs SET duplicate_of = ? WHERE url = ?", (target, r["url"]))
            changed += 1
    conn.commit()
    return changed


def redo_documents(conn: sqlite3.Connection | None = None, url: str | None = None,
                   applying_now: str | None = None) -> int:
    """Mark jobs to have their resume and cover letter written again.

    For one job (`url`, together with the other listings of the same role)
    or, with no url, every job that has documents. Nothing is deleted: the
    old documents stay (and the job stays ready to apply) until the new ones
    pass the checks, so a failed or interrupted rewrite loses nothing. Jobs
    already applied to are left alone, and so is `applying_now` (the job open
    in the browser at this moment). Jobs left "in_progress" by a run that
    never finished are put back in the apply queue.

    A listing waiting for Redo doesn't count as tailored when picking the
    canonical copy of a role, so the preferred site (`site_priority`,
    LinkedIn > Indeed > Jora by default) is the one rewritten; the mark
    moves to it. Returns the number of roles marked.
    """
    if conn is None:
        conn = get_connection()
    where = "applied_at IS NULL AND (? IS NULL OR url != ?)"
    params: list = [applying_now, applying_now]
    if url:
        group = conn.execute("SELECT COALESCE(duplicate_of, url) FROM jobs WHERE url = ?", (url,)).fetchone()
        if not group:
            return 0
        where += " AND (url = ? OR duplicate_of = ?)"
        params += [group[0], group[0]]
    else:
        where += (" AND (tailored_resume_path IS NOT NULL OR cover_letter_path IS NOT NULL "
                  "OR COALESCE(tailor_attempts, 0) > 0)")
    # Stale apply locks: nothing is applying to these any more
    conn.execute("UPDATE jobs SET apply_status = NULL, agent_id = NULL "
                 f"WHERE {where} AND apply_status = 'in_progress'", params)
    conn.execute(f"UPDATE jobs SET redo_requested = 1, tailor_attempts = 0 WHERE {where}", params)
    conn.commit()
    mark_duplicates(conn)
    # Only the canonical listing of a role is tailored: move the mark there
    conn.execute(
        "UPDATE jobs SET redo_requested = 1, tailor_attempts = 0 WHERE applied_at IS NULL AND (? IS NULL OR url != ?) "
        "AND url IN (SELECT duplicate_of FROM jobs WHERE redo_requested = 1 AND duplicate_of IS NOT NULL)",
        [applying_now, applying_now])
    conn.execute("UPDATE jobs SET redo_requested = 0 WHERE duplicate_of IS NOT NULL")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM jobs WHERE redo_requested = 1").fetchone()[0]


def finish_redo(conn: sqlite3.Connection, url: str) -> None:
    """A rewrite finished (passed or not): clear the mark on every listing of the role."""
    conn.execute("UPDATE jobs SET redo_requested = 0 WHERE url = ? OR duplicate_of = ?", (url, url))


def get_stats(conn: sqlite3.Connection | None = None) -> dict:
    """Return job counts by pipeline stage.

    Provides a snapshot of how many jobs are at each stage, useful for
    dashboard display and pipeline progress tracking.

    Args:
        conn: Database connection. Uses get_connection() if None.

    Returns:
        Dictionary with keys:
            total, by_site, pending_detail, with_description,
            scored, unscored, tailored, untailored_eligible,
            with_cover_letter, applied, score_distribution
    """
    if conn is None:
        conn = get_connection()

    stats: dict = {}

    # Total jobs
    stats["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # By site breakdown
    rows = conn.execute(
        "SELECT site, COUNT(*) as cnt FROM jobs GROUP BY site ORDER BY cnt DESC"
    ).fetchall()
    stats["by_site"] = [(row[0], row[1]) for row in rows]

    # Enrichment stage
    stats["pending_detail"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL"
    ).fetchone()[0]

    stats["with_description"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL"
    ).fetchone()[0]

    stats["detail_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE detail_error IS NOT NULL"
    ).fetchone()[0]

    # Scoring stage
    stats["scored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL"
    ).fetchone()[0]

    stats["unscored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE full_description IS NOT NULL AND fit_score IS NULL"
    ).fetchone()[0]

    # Score distribution
    dist_rows = conn.execute(
        "SELECT fit_score, COUNT(*) as cnt FROM jobs "
        "WHERE fit_score IS NOT NULL "
        "GROUP BY fit_score ORDER BY fit_score DESC"
    ).fetchall()
    stats["score_distribution"] = [(row[0], row[1]) for row in dist_rows]

    # Tailoring stage
    stats["tailored"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL"
    ).fetchone()[0]

    stats["untailored_eligible"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE fit_score >= 7 AND full_description IS NOT NULL "
        "AND tailored_resume_path IS NULL AND duplicate_of IS NULL"
    ).fetchone()[0]

    stats["tailor_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(tailor_attempts, 0) >= 5 "
        "AND tailored_resume_path IS NULL"
    ).fetchone()[0]

    # Cover letter stage
    stats["with_cover_letter"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE cover_letter_path IS NOT NULL"
    ).fetchone()[0]

    stats["cover_exhausted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE COALESCE(cover_attempts, 0) >= 5 "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '')"
    ).fetchone()[0]

    # Application stage
    stats["applied"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL"
    ).fetchone()[0]

    stats["apply_errors"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_error IS NOT NULL"
    ).fetchone()[0]

    stats["ready_to_apply"] = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE tailored_resume_path IS NOT NULL "
        "AND applied_at IS NULL AND duplicate_of IS NULL"
    ).fetchone()[0]

    stats["duplicates"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NOT NULL"
    ).fetchone()[0]

    return stats


def store_jobs(conn: sqlite3.Connection, jobs: list[dict],
               site: str, strategy: str) -> tuple[int, int]:
    """Store discovered jobs, skipping duplicates by URL.

    Args:
        conn: Database connection.
        jobs: List of job dicts with keys: url, title, salary, description, location.
        site: Source site name (e.g. "RemoteOK", "Dice").
        strategy: Extraction strategy used (e.g. "json_ld", "api_response", "css_selectors").

    Returns:
        Tuple of (new_count, duplicate_count).
    """
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, company, salary, description, location, site, strategy, "
                "discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), job.get("company"), job.get("salary"), job.get("description"),
                 job.get("location"), site, strategy, now),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


def get_jobs_by_stage(conn: sqlite3.Connection | None = None,
                      stage: str = "discovered",
                      min_score: int | None = None,
                      limit: int = 100) -> list[dict]:
    """Fetch jobs filtered by pipeline stage.

    Args:
        conn: Database connection. Uses get_connection() if None.
        stage: One of "discovered", "enriched", "scored", "tailored", "applied".
        min_score: Minimum fit_score filter (only relevant for scored+ stages).
        limit: Maximum number of rows to return.

    Returns:
        List of job dicts.
    """
    if conn is None:
        conn = get_connection()

    conditions = {
        "discovered": "1=1",
        "pending_detail": "detail_scraped_at IS NULL",
        "enriched": "full_description IS NOT NULL",
        "pending_score": "full_description IS NOT NULL AND fit_score IS NULL AND duplicate_of IS NULL",
        "scored": "fit_score IS NOT NULL",
        "pending_redo": (
            "redo_requested = 1 AND full_description IS NOT NULL AND duplicate_of IS NULL "
            "AND applied_at IS NULL"
        ),
        "pending_tailor": (
            "fit_score >= ? AND full_description IS NOT NULL AND duplicate_of IS NULL "
            "AND tailored_resume_path IS NULL AND COALESCE(tailor_attempts, 0) < 5"
        ),
        "tailored": "tailored_resume_path IS NOT NULL",
        "pending_apply": (
            "tailored_resume_path IS NOT NULL AND applied_at IS NULL "
            "AND application_url IS NOT NULL AND duplicate_of IS NULL"
        ),
        "applied": "applied_at IS NOT NULL",
    }

    where = conditions.get(stage, "1=1")
    params: list = []

    if "?" in where and min_score is not None:
        params.append(min_score)
    elif "?" in where:
        params.append(7)  # default min_score

    if min_score is not None and "fit_score" not in where and stage in ("scored", "tailored", "applied"):
        where += " AND fit_score >= ?"
        params.append(min_score)

    query = f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC NULLS LAST, discovered_at DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    # Convert sqlite3.Row objects to dicts
    if rows:
        columns = rows[0].keys()
        return [dict(zip(columns, row)) for row in rows]
    return []

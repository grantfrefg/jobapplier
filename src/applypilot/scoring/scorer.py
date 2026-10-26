"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. Titles in searches.yaml `exclude_titles` are skipped
without an LLM call; the rest are scored several per request in JSON mode,
since free LLM tiers limit requests per day.
"""

import logging
import re
import time
from datetime import datetime, timezone

from applypilot.config import DEFAULTS, RESUME_PATH, load_search_config
from applypilot.database import get_connection, get_jobs_by_stage, mark_duplicates
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and job postings, score how well
the candidate fits each role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

You will receive the candidate's resume and one or more job postings, each with an "id".
Score every posting independently.

Return ONLY a JSON object of this shape, with exactly one entry per posting:
{"results": [{"id": "<posting id>", "score": <integer 1-10>,
              "keywords": "<comma-separated ATS keywords from the posting that match or could match the candidate>",
              "reasoning": "<2-3 sentences explaining the score>"}]}"""

# Description characters sent per job (most postings are well under this)
_MAX_DESCRIPTION_CHARS = 6000


def title_excluded(title: str | None, patterns: list[str]) -> str | None:
    """The first exclude_titles pattern found in the title (whole words, any case)."""
    t = (title or "").lower()
    for pattern in patterns:
        p = str(pattern).strip().lower()
        if p and re.search(rf"(?<!\w){re.escape(p)}(?!\w)", t):
            return str(pattern).strip()
    return None


def _job_block(job_id: str, job: dict) -> str:
    return (
        f"### POSTING id={job_id}\n"
        f"TITLE: {job.get('title') or '?'}\n"
        f"COMPANY: {job.get('company') or 'unknown'}\n"
        f"LOCATION: {job.get('location') or 'N/A'}\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:_MAX_DESCRIPTION_CHARS]}"
    )


def score_batch(resume_text: str, jobs: list[dict]) -> dict[str, dict]:
    """Score several jobs in one LLM request.

    Returns {url: {"score", "keywords", "reasoning"}} for every job the model
    scored validly. Jobs missing from the reply are left out (and stay
    unscored, so the next run retries them). Raises on request failure.
    """
    ids = {f"j{i}": job for i, job in enumerate(jobs, 1)}
    postings = "\n\n".join(_job_block(job_id, job) for job_id, job in ids.items())
    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTINGS ({len(ids)}):\n\n{postings}"},
    ]
    # ~150 output tokens per job is plenty for score + keywords + reasoning
    data = get_client().chat_json(messages, temperature=0.2, max_tokens=300 + 250 * len(ids))
    entries = data.get("results", data) if isinstance(data, dict) else data

    scored: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        job = ids.get(str(entry.get("id", "")).strip())
        try:
            score = int(entry.get("score"))
        except (TypeError, ValueError):
            continue
        if job is None or not 1 <= score <= 10:
            continue
        keywords = entry.get("keywords", "")
        if isinstance(keywords, list):
            keywords = ", ".join(map(str, keywords))
        scored[job["url"]] = {"score": score, "keywords": str(keywords),
                              "reasoning": str(entry.get("reasoning", ""))}
    return scored


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job (kept for callers that score one at a time)."""
    try:
        result = score_batch(resume_text, [job]).get(job["url"])
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": 0, "keywords": "", "reasoning": f"LLM error: {e}"}
    return result or {"score": 0, "keywords": "", "reasoning": "LLM returned no score"}


def run_scoring(limit: int = 0, rescore: bool = False, batch_size: int | None = None) -> dict:
    """Score unscored jobs that have full descriptions.

    Jobs whose title matches searches.yaml `exclude_titles` get score 0
    without an LLM call. The rest are scored `batch_size` at a time (default
    config.DEFAULTS["score_batch_size"]) in one request per batch. Jobs that
    fail to score stay unscored and are retried on the next run.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).
        batch_size: Jobs per LLM request.

    Returns:
        {"scored": int, "skipped": int, "errors": int, "requests": int,
         "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    search_cfg = load_search_config() or {}
    exclude = [str(t) for t in search_cfg.get("exclude_titles") or []]
    batch_size = max(1, int(batch_size or search_cfg.get("score_batch_size")
                            or DEFAULTS["score_batch_size"]))

    conn = get_connection()
    # Older versions stored score 0 when the LLM call failed (e.g. a bad API
    # key), which meant those jobs were never scored. Give them another go.
    conn.execute(
        "UPDATE jobs SET fit_score = NULL, score_reasoning = NULL, scored_at = NULL "
        "WHERE fit_score = 0 AND score_reasoning LIKE '%LLM error:%'"
    )
    conn.commit()
    mark_duplicates(conn)  # skip roles already found on another site

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "skipped": 0, "errors": 0, "requests": 0, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    t0 = time.time()
    now = datetime.now(timezone.utc).isoformat()

    # 1. Free filter: excluded titles never reach the LLM
    to_score: list[dict] = []
    skipped = 0
    for job in jobs:
        hit = title_excluded(job.get("title"), exclude)
        if hit:
            conn.execute(
                "UPDATE jobs SET fit_score = 0, score_reasoning = ?, scored_at = ? WHERE url = ?",
                (f"\nSkipped: title contains '{hit}' (exclude_titles)", now, job["url"]),
            )
            skipped += 1
        else:
            to_score.append(job)
    conn.commit()
    if skipped:
        log.info("Skipped %d jobs by title (exclude_titles), no LLM requests used.", skipped)

    # 2. Batched LLM scoring
    batches = [to_score[i:i + batch_size] for i in range(0, len(to_score), batch_size)]
    log.info("Scoring %d jobs in %d request(s) of up to %d...", len(to_score), len(batches), batch_size)
    scored = errors = 0
    for n, batch in enumerate(batches, 1):
        try:
            results = score_batch(resume_text, batch)
        except Exception as e:
            log.error("Batch %d/%d failed (%d jobs left unscored for next run): %s", n, len(batches), len(batch), e)
            errors += len(batch)
            continue
        for job in batch:
            r = results.get(job["url"])
            if r is None:
                errors += 1
                log.warning("No score returned for '%s'; will retry next run.", job.get("title", "?")[:60])
                continue
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
                (r["score"], f"{r['keywords']}\n{r['reasoning']}", now, job["url"]),
            )
            scored += 1
            log.info("score=%d  %s", r["score"], job.get("title", "?")[:60])
        conn.commit()

    elapsed = time.time() - t0
    log.info("Done: %d scored, %d skipped by title, %d unscored, %d LLM request(s), %.1fs",
             scored, skipped, errors, len(batches), elapsed)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": scored,
        "skipped": skipped,
        "errors": errors,
        "requests": len(batches),
        "elapsed": elapsed,
        "distribution": distribution,
    }

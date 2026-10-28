"""Run one pipeline step again for jobs that already went through it.

Each stage normally skips jobs it has done (`applypilot run`), so this is how
you redo a single step: for one job (the Apply tab's "Run again" menu) or for
every job not applied to yet (Find jobs → Run a step again, or
`applypilot rerun STEP`). What a job already has is kept until the new
version is ready: a failed re-score keeps the old score, a cover letter that
fails its checks keeps the old letter, and so on.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from applypilot import config

log = logging.getLogger(__name__)

# step -> (what it does, AI requests per job, which jobs it can run on)
STEPS: dict[str, tuple[str, str, str]] = {
    "enrich": ("Get the job's description and application link again", "AI only for pages it can't read",
               "1 = 1"),
    "score": ("Score the job against your resume again", "about 1 request per 10 jobs",
              "full_description IS NOT NULL"),
    "tailor": ("Write the resume and cover letter again (Redo)", "about 2 requests per job",
               "full_description IS NOT NULL"),
    "cover": ("Write just the cover letter again, keeping the resume", "1 or 2 requests per job",
              "tailored_resume_path IS NOT NULL AND full_description IS NOT NULL"),
    "review": ("Fact-check the resume and cover letter again against your resume", "1 request per job",
               "tailored_resume_path IS NOT NULL"),
    "pdf": ("Make the PDFs again from the documents' text", "no AI",
            "(tailored_resume_path IS NOT NULL OR cover_letter_path IS NOT NULL)"),
    "ats": ("Run the ATS check again", "no AI",
            "(tailored_resume_path IS NOT NULL OR own_resume_path IS NOT NULL)"),
}
# Steps that come after scoring only run on jobs at or above the minimum score
# when no job is named
_SCORED_STEPS = {"tailor", "cover", "review", "pdf", "ats"}


def targets(conn, step: str, urls: list[str] | None = None, min_score: int | None = None) -> list[dict]:
    """The jobs a step would run on: the named ones, else every job not applied to yet."""
    if step not in STEPS:
        raise ValueError(f"step must be one of {', '.join(STEPS)}")
    where = f"applied_at IS NULL AND duplicate_of IS NULL AND {STEPS[step][2]}"
    params: list = []
    if urls:
        where += f" AND url IN ({','.join('?' * len(urls))})"
        params += list(urls)
    elif min_score is not None and step in _SCORED_STEPS:
        where += " AND fit_score >= ?"
        params.append(min_score)
    if step == "enrich":
        from applypilot.enrichment.detail import SKIP_DETAIL_SITES
        where += f" AND site NOT IN ({','.join('?' * len(SKIP_DETAIL_SITES))})"
        params += sorted(SKIP_DETAIL_SITES)
    rows = conn.execute(f"SELECT * FROM jobs WHERE {where} ORDER BY fit_score DESC NULLS LAST", params).fetchall()
    return [dict(r) for r in rows]


def run(step: str, urls: list[str] | None = None, min_score: int | None = None,
        validation: str = "normal") -> int:
    """Run `step` again; returns the number of jobs it ran on."""
    from applypilot.database import get_connection, mark_duplicates
    conn = get_connection()
    mark_duplicates(conn)
    jobs = targets(conn, step, urls, min_score)
    if not jobs:
        log.info("No jobs to run '%s' again for.", step)
        return 0
    log.info("%s: %d job(s) (%s)", STEPS[step][0], len(jobs), STEPS[step][1])
    {"enrich": _enrich, "score": _score, "tailor": _tailor, "cover": _cover,
     "review": _review, "pdf": _pdf, "ats": _ats}[step](conn, jobs, validation)
    return len(jobs)


# -- steps -------------------------------------------------------------------

def _enrich(conn, jobs: list[dict], validation: str) -> None:
    from applypilot.enrichment.detail import SITE_DELAYS, scrape_site_batch
    by_site: dict[str, list[tuple]] = {}
    for job in jobs:
        by_site.setdefault(job["site"], []).append((job["url"], job["title"]))
    for site, batch in by_site.items():
        stats = scrape_site_batch(conn, site, batch, delay=SITE_DELAYS.get(site, 2.0))
        log.info("%s: %d ok, %d partial, %d error (a page that fails keeps the description it had)",
                 site, stats["ok"], stats["partial"], stats["error"])


def _score(conn, jobs: list[dict], validation: str) -> None:
    from applypilot.scoring.scorer import run_scoring
    run_scoring(urls=[j["url"] for j in jobs])


def _tailor(conn, jobs: list[dict], validation: str) -> None:
    from applypilot.database import redo_documents
    from applypilot.scoring.pdf import batch_convert
    from applypilot.scoring.tailor import run_tailoring
    for job in jobs:
        redo_documents(conn, job["url"])
    result = run_tailoring(limit=0, validation_mode=validation, redo_only=True)
    batch_convert()
    log.info("Done: %d written. Jobs whose new version didn't pass keep their old documents.", result["approved"])


def _prefix(job: dict) -> str:
    title = re.sub(r"[^\w\s-]", "", job["title"] or "")[:50].strip().replace(" ", "_")
    site = re.sub(r"[^\w\s-]", "", job.get("company") or job["site"] or "")[:20].strip().replace(" ", "_")
    return f"{site}_{title}"


def _render(path: Path) -> None:
    from applypilot.scoring.pdf import convert_to_pdf
    try:
        convert_to_pdf(path)
    except Exception as e:
        log.error("Couldn't make the PDF of %s: %s", path.name, e)


def _cover(conn, jobs: list[dict], validation: str) -> None:
    from applypilot.scoring.cover_letter import generate_cover_letter
    profile = config.load_profile()
    resume_text = config.RESUME_PATH.read_text(encoding="utf-8")
    config.COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    for n, job in enumerate(jobs, 1):
        stats: dict = {"requests": 0}
        letter = generate_cover_letter(resume_text, job, profile, max_retries=1,
                                       validation_mode=validation, stats=stats)
        if not letter:
            log.info("%d/%d [KEPT OLD] %s: every new letter claimed something your resume doesn't",
                     n, len(jobs), job["title"][:40])
            continue
        path = Path(job["cover_letter_path"]) if job.get("cover_letter_path") else \
            config.COVER_LETTER_DIR / f"{_prefix(job)}_CL.txt"
        path.write_text(letter, encoding="utf-8")
        _render(path)
        conn.execute("UPDATE jobs SET cover_letter_path = ?, cover_letter_at = ? WHERE url = ?",
                     (str(path), datetime.now(timezone.utc).isoformat(), job["url"]))
        conn.commit()
        log.info("%d/%d [WRITTEN] requests=%d | %s", n, len(jobs), stats["requests"], job["title"][:40])


def _review(conn, jobs: list[dict], validation: str) -> None:
    """The AI fact-check (the tailor step's judge) on the documents the job has now.

    A job that fails keeps its documents but shows the problems on the Apply
    tab, so you can Redo it or fix the resume before applying.
    """
    from applypilot.scoring.tailor import judge_tailored_resume
    profile = config.load_profile()
    original = config.RESUME_PATH.read_text(encoding="utf-8")
    for n, job in enumerate(jobs, 1):
        try:
            resume = Path(job["tailored_resume_path"]).read_text(encoding="utf-8")
        except OSError:
            log.info("%d/%d [SKIPPED] %s: its resume file is missing", n, len(jobs), job["title"][:40])
            continue
        letter = ""
        if job.get("cover_letter_path"):
            try:
                letter = Path(job["cover_letter_path"]).read_text(encoding="utf-8")
            except OSError:
                pass
        verdict = judge_tailored_resume(original, resume, job.get("title") or "", profile, letter,
                                        job_ad=job.get("full_description") or "")
        if verdict["passed"]:
            conn.execute("UPDATE jobs SET tailor_status = 'approved', tailor_issues = NULL WHERE url = ?",
                         (job["url"],))
            log.info("%d/%d [PASSED] %s", n, len(jobs), job["title"][:40])
        else:
            conn.execute("UPDATE jobs SET tailor_status = 'review_failed', tailor_issues = ? WHERE url = ?",
                         (verdict["issues"][:500], job["url"]))
            log.info("%d/%d [FLAGGED] %s", n, len(jobs), job["title"][:40])
            log.info("    The fact-check found: %s. The documents are kept: read them, then Redo the job or "
                     "apply as they are.", verdict["issues"][:300])
        conn.commit()


def _pdf(conn, jobs: list[dict], validation: str) -> None:
    for job in jobs:
        for col in ("tailored_resume_path", "cover_letter_path"):
            if job.get(col) and Path(job[col]).exists():
                _render(Path(job[col]))
    _ats(conn, [j for j in jobs if j.get("tailored_resume_path")], validation)  # the check reads the PDF


def _ats(conn, jobs: list[dict], validation: str) -> None:
    from applypilot import own_docs
    from applypilot.scoring import ats
    original = config.RESUME_PATH.read_text(encoding="utf-8") if config.RESUME_PATH.exists() else ""
    for job in jobs:
        if job.get("own_resume_path") and Path(job["own_resume_path"]).exists():
            own_docs._record_ats(conn, job["url"], Path(job["own_resume_path"]))  # your file is what's sent
        else:
            try:
                path = Path(job["tailored_resume_path"])
                pdf = path.with_suffix(".pdf")
                report = ats.check(path.read_text(encoding="utf-8"), job, original,
                                   pdf_path=pdf if pdf.exists() else None)
            except (OSError, TypeError):
                continue
            conn.execute("UPDATE jobs SET ats_score = ?, ats_report = ? WHERE url = ?",
                         (report["score"], json.dumps(report), job["url"]))
        conn.commit()
        row = conn.execute("SELECT ats_score FROM jobs WHERE url = ?", (job["url"],)).fetchone()
        log.info("ATS %s  %s", row[0] if row and row[0] is not None else "–", job["title"][:50])

"""Cover letter generation: LLM-powered, profile-driven, with validation.

Generates concise, engineering-voice cover letters tailored to specific job
postings. All personal data (name, skills, achievements) comes from the user's
profile at runtime. No hardcoded personal information.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from applypilot.budget import BudgetExhausted
from applypilot.config import COVER_LETTER_DIR, RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage, mark_duplicates
from applypilot.llm import get_client
from applypilot.scoring.context import AU_STYLE, target_role, years
from applypilot.scoring.validator import (
    BANNED_WORDS,
    LLM_LEAK_PHRASES,
    sanitize_text,
    validate_cover_letter,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


# ── Prompt Builder (profile-driven) ──────────────────────────────────────

def sign_off_name(profile: dict) -> str:
    """Preferred name for the sign-off (falls back to full name)."""
    personal = profile.get("personal", {})
    return personal.get("preferred_name") or personal.get("full_name", "")


def cover_letter_rules(profile: dict) -> str:
    """The cover letter writing rules (structure, voice, banned words, skills).

    Shared by the standalone prompt below and the combined resume + cover
    letter request in tailor.py.
    """
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})
    name = sign_off_name(profile)
    yrs = years(profile)
    role_line = f"a {target_role(profile)}" + (f" with {yrs} years of experience" if yrs else "")

    # Flatten all allowed skills
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) if all_skills else "the tools listed in the resume"

    # Real metrics from resume_facts
    real_metrics = resume_facts.get("real_metrics", [])
    preserved_projects = resume_facts.get("preserved_projects", [])

    # Build achievement examples for the prompt
    projects_hint = ""
    if preserved_projects:
        projects_hint = f"\nKnown projects to reference: {', '.join(preserved_projects)}"

    metrics_hint = ""
    if real_metrics:
        metrics_hint = f"\nReal metrics to use: {', '.join(real_metrics)}"

    # Build the full banned list from the validator so the prompt stays in sync
    # with what will actually be rejected — the validator checks all of these.
    all_banned = ", ".join(f'"{w}"' for w in BANNED_WORDS)
    leak_banned = ", ".join(f'"{p}"' for p in LLM_LEAK_PHRASES)

    return f"""Write a cover letter for {name}, {role_line}. The goal is to get an interview with the
engineers and hiring manager reading it.

STRUCTURE: 3 short paragraphs, under 250 words. Every sentence must earn its place.

PARAGRAPH 1 (2-3 sentences): Open with the most relevant thing the candidate has built, designed or
delivered, and how it maps to what this team needs. Not "I'm excited to apply." Start with the work.

PARAGRAPH 2 (3-4 sentences): 1-2 more achievements from the resume most relevant to THIS job, with
real numbers or scale (users, requests, data volume, uptime, cost, time saved). Frame them as the
problems this team has.{projects_hint}{metrics_hint}

PARAGRAPH 3 (1-2 sentences): One specific reason for THIS role, taken from the job description (the
product, system, technical challenge or team). If the employer isn't named (e.g. a recruitment agency
ad), talk about the role and project described instead. Never invent facts about the company. Close
plainly, e.g. "Happy to walk through any of this in more detail."

BANNED WORDS AND PHRASES (automated validator rejects ANY of these, do not use even once):
{all_banned}

ALSO BANNED (meta-commentary the validator catches):
{leak_banned}

BANNED PUNCTUATION: No em dashes (—) or en dashes (–). Use commas or periods.

VOICE:
- An engineer writing to another engineer: direct, specific, confident, no fluff.
- Vary sentence length so it reads like a person. Most sentences should carry a concrete detail
  (a system, a tool, a number, an outcome); cut any that carry none.
- NEVER narrate or explain. BAD: "This demonstrates my commitment to X." GOOD: state the fact.
- NEVER hedge. BAD: "might address some of your challenges." GOOD: "solves the same problem."
- {AU_STYLE}

FABRICATION = INSTANT REJECTION:
The candidate's real tools are ONLY: {skills_str}.
Do NOT mention ANY tool not in this list. If the job asks for tools not listed, talk about the
closest real work, not the tool.
Do NOT use any number that isn't in the resume.

Start with "Dear <name>," if the job ad names the contact person, otherwise "Dear Hiring Manager,".
Sign off with just "{name}"."""


def _build_cover_letter_prompt(profile: dict) -> str:
    """Build the standalone cover letter system prompt from the user's profile."""
    return cover_letter_rules(profile) + """

Output ONLY the letter text. No subject lines. No "Here is the cover letter:" preamble. No notes after the sign-off."""


def job_text(job: dict) -> str:
    """The target job as shown to the LLM (shared with tailor.py)."""
    return (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job.get('company') or job.get('site') or 'unknown'}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
        + _matched_terms(job)
    )


def _matched_terms(job: dict) -> str:
    """Must-haves the scorer found in the resume (first line of score_reasoning)."""
    first = (job.get("score_reasoning") or "").split("\n", 1)[0].strip()
    return f"\n\nMUST-HAVES THE RESUME SHOWS (lead with these): {first[:400]}" if first else ""


# ── Helpers ──────────────────────────────────────────────────────────────

def clean_letter(text: str) -> str:
    """Auto-fix dashes/quotes and drop any preamble before 'Dear ...'."""
    return _strip_preamble(sanitize_text(text or ""))


def _strip_preamble(text: str) -> str:
    """Remove LLM preamble before 'Dear Hiring Manager,' if present.

    Gemini and other models sometimes output "Here is the cover letter:" or
    similar meta-commentary before the actual letter text. Strip everything
    before the first occurrence of "Dear" so the validator's start-check passes.
    """
    dear_idx = text.lower().find("dear")
    if dear_idx > 0:
        return text[dear_idx:]
    return text


# ── Core Generation ──────────────────────────────────────────────────────

def generate_cover_letter(
    resume_text: str, job: dict, profile: dict,
    max_retries: int = 3, validation_mode: str = "normal",
    stats: dict | None = None,
) -> str:
    """Generate a cover letter with fresh context on each retry + auto-sanitize.

    Same design as tailor_resume: fresh conversation per attempt, issues noted
    in the prompt, no conversation history stacking.

    Args:
        resume_text:      The candidate's resume text (base or tailored).
        job:              Job dict with title, site, location, full_description.
        profile:          User profile dict.
        max_retries:      Maximum retry attempts.
        validation_mode:  "strict", "normal", or "lenient".
        stats:            If given, stats["requests"] is incremented per LLM call.

    Returns:
        The cover letter text (best attempt even if validation failed).
    """
    target = job_text(job)

    avoid_notes: list[str] = []
    letter = ""
    client = get_client()
    cl_prompt_base = _build_cover_letter_prompt(profile)

    for attempt in range(max_retries + 1):
        # Fresh conversation every attempt
        prompt = cl_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES:\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"RESUME:\n{resume_text}\n\n---\n\n"
                f"TARGET JOB:\n{target}\n\n"
                "Write the cover letter:"
            )},
        ]

        if stats is not None:
            stats["requests"] = stats.get("requests", 0) + 1
        letter = clean_letter(client.chat(messages, max_tokens=1024, temperature=0.7))

        validation = validate_cover_letter(letter, mode=validation_mode)
        if validation["passed"]:
            return letter

        avoid_notes.extend(validation["errors"])
        # Warnings never block — only hard errors trigger a retry
        log.debug(
            "Cover letter attempt %d/%d failed: %s",
            attempt + 1, max_retries + 1, validation["errors"],
        )

    return letter  # last attempt even if failed


# ── Batch Entry Point ────────────────────────────────────────────────────

def run_cover_letters(min_score: int = 7, limit: int = 20,
                      validation_mode: str = "normal") -> dict:
    """Generate cover letters for high-scoring jobs that have tailored resumes.

    Args:
        min_score:       Minimum fit_score threshold.
        limit:           Maximum jobs to process.
        validation_mode: "strict", "normal", or "lenient".

    Returns:
        {"generated": int, "errors": int, "elapsed": float}
    """
    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    mark_duplicates(conn)  # skip roles already found on another site

    # Fetch jobs that have tailored resumes but no cover letter yet
    jobs = conn.execute(
        "SELECT * FROM jobs "
        "WHERE fit_score >= ? AND tailored_resume_path IS NOT NULL "
        "AND full_description IS NOT NULL AND duplicate_of IS NULL "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
        "AND COALESCE(cover_attempts, 0) < ? "
        "ORDER BY fit_score DESC LIMIT ?",
        (min_score, MAX_ATTEMPTS, limit),
    ).fetchall()

    if not jobs:
        log.info("No jobs needing cover letters (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert rows to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    log.info(
        "Generating cover letters for %d jobs (score >= %d)...",
        len(jobs), min_score,
    )
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    error_count = 0

    for job in jobs:
        completed += 1
        try:
            letter = generate_cover_letter(resume_text, job, profile,
                                          validation_mode=validation_mode)

            # Build safe filename prefix
            safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50].strip().replace(" ", "_")
            safe_site = re.sub(r"[^\w\s-]", "", job["site"])[:20].strip().replace(" ", "_")
            prefix = f"{safe_site}_{safe_title}"

            cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
            cl_path.write_text(letter, encoding="utf-8")

            # Generate PDF (best-effort)
            pdf_path = None
            try:
                from applypilot.scoring.pdf import convert_to_pdf
                pdf_path = str(convert_to_pdf(cl_path))
            except Exception:
                log.debug("PDF generation failed for %s", cl_path, exc_info=True)

            result = {
                "url": job["url"],
                "path": str(cl_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
            }
            results.append(result)

            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            log.info(
                "%d/%d [OK] | %.1f jobs/min | %s",
                completed, len(jobs), rate * 60, result["title"][:40],
            )
        except BudgetExhausted as e:
            log.warning("%s Stopping: %d jobs left for the next run.", e, len(jobs) - completed + 1)
            break
        except Exception as e:
            result = {
                "url": job["url"], "title": job["title"], "site": job["site"],
                "path": None, "pdf_path": None, "error": str(e),
            }
            error_count += 1
            results.append(result)
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

    # Persist to DB: increment attempt counter for ALL, save path only for successes
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for r in results:
        if r.get("path"):
            conn.execute(
                "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, "
                "cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
            saved += 1
        else:
            conn.execute(
                "UPDATE jobs SET cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Cover letters done in %.1fs: %d generated, %d errors", elapsed, saved, error_count)

    return {
        "generated": saved,
        "errors": error_count,
        "elapsed": elapsed,
    }

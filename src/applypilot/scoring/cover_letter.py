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
from applypilot.scoring import voice
from applypilot.scoring.context import AU_STYLE, current_title, years
from applypilot.scoring.validator import (
    BANNED_WORDS,
    australianise,
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
    from applypilot.scoring.facts import profile_skills
    resume_facts = profile.get("resume_facts", {})
    name = sign_off_name(profile)
    yrs = years(profile)
    now = current_title(profile)
    # Their real title: the letter must never present them as the target role
    role_line = (f"currently a {now}" if now else "the candidate") + (f", {yrs} years of experience" if yrs else "")
    extra = profile_skills(profile)
    skills_str = "the tools named in the resume" + (f", plus {', '.join(extra)}" if extra else "")

    # Relocation or sponsorship: one plain sentence when it matters, never otherwise
    auth = profile.get("work_authorization", {})
    avail = profile.get("availability", {})
    city = profile.get("personal", {}).get("city") or ""
    situation = ""
    when = [f"{label}: {avail[key]}" for key, label in (("notice_period", "notice period"),
                                                         ("earliest_start_date", "earliest start"))
            if str(avail.get(key) or "").strip()]
    if when:
        situation += f"\nAvailability, if the ad asks: {'; '.join(when)}."
    if str(auth.get("require_sponsorship") or "").strip().lower() in ("yes", "true", "y"):
        situation += ("\nThe candidate needs visa sponsorship: say so in one plain sentence near the end "
                     "(employers ask anyway, and hiding it wastes everyone's time).")
    if str(avail.get("willing_to_relocate") or "").strip().lower() in ("yes", "true", "y") and city:
        situation += (f"\nThe candidate lives in {city} and will relocate: if the role is in another city, say so "
                      "in one plain sentence. If it's in their city or remote, don't mention location.")

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

STRUCTURE: 3-4 short paragraphs, 200-300 words, one page. Every sentence must earn its place.

OPENING (2-3 sentences): Name the role, then go straight to the most relevant thing the candidate has
built, designed or delivered and how it maps to what this team needs. Not "I am writing to express my
interest" and not "I'm excited to apply". Start with the work.

MIDDLE (one or two paragraphs): 1-2 more achievements from the resume most relevant to THIS job, with
real numbers or scale where the resume has them. Use the ad's own words for the skills it asks for
that the candidate really has (recruiters and ATS search for them).{projects_hint}{metrics_hint}
If the ad asks applicants to address selection criteria (common in Australian government and
university roles), touch on each one briefly with a real example from the resume.
If the ad asks the letter to cover something specific (availability or start date, why this team, a
question to answer), answer it, using only facts the candidate gave.{situation}

CLOSE (1-2 sentences): One specific reason for THIS role, taken from the job description (the
product, system, technical challenge or team). If the employer isn't named (e.g. a recruitment agency
ad), talk about the role and project described instead. Never invent facts about the company. Then a
plain close, e.g. "Happy to walk through any of this in more detail." No "I would be a great fit".

BANNED WORDS AND PHRASES (automated validator rejects ANY of these, do not use even once):
{all_banned}

ALSO BANNED (meta-commentary the validator catches):
{leak_banned}

BANNED PUNCTUATION: No em dashes (—) or en dashes (–). Use commas or periods.

VOICE: IT MUST NOT READ AS AI-WRITTEN (recruiters reject letters that do):
- An engineer writing to another engineer, Australian tone: direct, specific, understated. Confident
  without selling; no superlatives, no gushing, no exclamation marks, no questions.
- Vary sentence length and how sentences start (not every one with "I"). Most sentences carry a
  concrete detail (a system, a tool, a number, an outcome); cut any that carry none.
- No "not just X but Y", no "Whether it's...", no lists of three adjectives.
- NEVER narrate or explain. BAD: "This demonstrates my commitment to X." GOOD: state the fact.
- NEVER hedge. BAD: "might address some of your challenges." GOOD: "solves the same problem."
- {AU_STYLE}

FABRICATION = INSTANT REJECTION (code checks every one of these against the resume):
- The candidate's real tools are ONLY {skills_str}. Do NOT mention any other tool, even one the job
  asks for: talk about the closest real work instead.
- Do NOT use any number that isn't in the resume.
- Never call the candidate by a job title they haven't held ("as an AI engineer" for a data
  scientist). Name the job you're applying for only as the role ("the AI Engineer role").
- Don't join separate facts into a new claim, and don't claim experience the resume doesn't show.

Start with "Dear <name>," if the job ad names the contact person, otherwise "Dear Hiring Manager,".
End with "Kind regards," on its own line, then "{name}".{voice.letter_voice_rules()}"""


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

def _fact_errors(letter: str, resume_text: str, profile: dict, job_ad: str = "") -> list[str]:
    from applypilot.scoring import facts
    from applypilot.scoring.tailor import letter_problems
    return letter_problems(letter, facts.source_for(resume_text, profile), resume_text, job_ad)


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
    fact_errors: list[str] = []
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
        letter = australianise(clean_letter(client.chat(messages, max_tokens=1024, temperature=0.7)), resume_text)

        validation = validate_cover_letter(letter, mode=validation_mode, original_text=resume_text)
        fact_errors = _fact_errors(letter, resume_text, profile, job.get("full_description") or "")
        validation["errors"] += fact_errors
        same = voice.repeated_opening(letter, voice.recent_openings())
        if same:  # a style problem: retried, but never a reason to drop the letter
            validation["errors"].append(f'Opens like a recent letter ("{same[:80]}"): open differently.')
        if not validation["errors"]:
            return letter

        avoid_notes.extend(validation["errors"])
        # Warnings never block — only hard errors trigger a retry
        log.debug(
            "Cover letter attempt %d/%d failed: %s",
            attempt + 1, max_retries + 1, validation["errors"],
        )

    if fact_errors:
        log.warning("No cover letter: every attempt claimed things the resume doesn't (%s)", fact_errors[0])
        return ""  # better no letter than one that claims what the resume doesn't
    return letter  # last attempt, even if it failed a style check


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

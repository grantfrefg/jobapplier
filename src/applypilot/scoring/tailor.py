"""Resume tailoring: LLM-powered ATS-optimized resume generation per job.

THIS IS THE HEAVIEST REFACTOR. Every piece of personal data -- name, email, phone,
skills, companies, projects, school -- is loaded at runtime from the user's profile.
Zero hardcoded personal information.

The LLM returns structured JSON, code assembles the final text. Header (name, contact)
is always code-injected, never LLM-generated. Each retry starts a fresh conversation
to avoid apologetic spirals.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot.budget import BudgetExhausted
from applypilot.config import RESUME_PATH, TAILORED_DIR, load_profile
from applypilot.database import get_connection, get_jobs_by_stage, mark_duplicates
from applypilot.llm import get_client
from applypilot.scoring.context import AU_STYLE
from applypilot.scoring.cover_letter import (
    COVER_LETTER_DIR,
    clean_letter,
    cover_letter_rules,
    generate_cover_letter,
    job_text,
)
from applypilot.scoring.validator import (
    BANNED_WORDS,
    FABRICATION_WATCHLIST,
    new_numbers,
    sanitize_text,
    validate_cover_letter,
    validate_json_fields,
    validate_tailored_resume,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


# ── Prompt Builders (profile-driven) ──────────────────────────────────────

def _tailor_rules(profile: dict) -> str:
    """The resume tailoring rules, derived from the user's profile.

    All skills boundaries, preserved entities, and formatting rules are
    derived from the profile -- nothing is hardcoded.
    """
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Format skills boundary for the prompt
    skills_lines = []
    for category, items in boundary.items():
        if isinstance(items, list) and items:
            label = category.replace("_", " ").title()
            skills_lines.append(f"{label}: {', '.join(items)}")
    skills_block = "\n".join(skills_lines)

    # Preserved entities
    companies = resume_facts.get("preserved_companies", [])
    projects = resume_facts.get("preserved_projects", [])
    school = resume_facts.get("preserved_school", "")
    real_metrics = resume_facts.get("real_metrics", [])

    companies_str = ", ".join(companies) if companies else "N/A"
    projects_str = ", ".join(projects) if projects else "N/A"
    metrics_str = ", ".join(real_metrics) if real_metrics else "only those in the original resume"

    # Include ALL banned words from the validator so the LLM knows exactly
    # what will be rejected — the validator checks for these automatically.
    banned_str = ", ".join(BANNED_WORDS)

    return f"""You are a senior engineering recruiter in Australia rewriting a resume so this candidate
gets an interview for the target job. Return the tailored resume as a JSON object.

## WHAT THE HIRING ENGINEER CHECKS FIRST (about 6 seconds)
1. Title: does it match what they're hiring?
2. Summary: 2-3 sentences showing they've done this work, at this level.
3. First 3 bullets of the most recent role: the job's must-haves, with outcomes.
4. Skills: the must-haves visible at a glance.

## SKILLS (real skills only)
List only skills that appear in the original resume or in this boundary:
{skills_block}
Never add a tool, language, framework, standard or certification the candidate doesn't have, even a
closely related one. If the job wants something they lack, emphasise the nearest real experience instead.

## TAILORING RULES
TITLE: Use the target job's title when it honestly describes the candidate's work (e.g. "Software
Engineer" -> "Backend Engineer" for backend work). Keep their seniority. Never change discipline.
Drop company names and team names from the title.

SUMMARY: Rewrite for this job. Lead with the 1-2 things that matter most for THIS role, with a real
number or scale if the resume has one. Sound like someone who already does this job.

SKILLS: Group into categories that suit the candidate (e.g. Languages, Frameworks, Cloud & DevOps,
Databases, Tools; or for non-software engineering: Design Software, Standards & Codes, Methods).
Put the job's must-haves first in each category. Drop skills irrelevant to this job if space is tight.

BULLETS: Reorder so the job's must-haves come first. Reword a bullet when the job uses different
terms for the same work (e.g. "web services" -> "REST APIs") or when its impact is buried; keep it as
is when it already fits. Shape: strong verb + what was built/designed/delivered + scale or result.
Vary verbs (Built, Designed, Led, Shipped, Migrated, Automated, Reduced, Scaled). Up to 5 bullets for
recent roles, 2-3 for older ones.

PROJECTS: Most relevant first. Drop projects that don't help for this job.

LENGTH: Australian resumes are normally 2 pages. Keep it tight: no filler bullets.

## VOICE
- Write like an engineer: plain, specific, technical where it helps.
- GOOD: "Built a Python ingestion service on AWS Lambda for 2M events a day, cut reporting lag
  from 6 hours to 15 minutes"
- BAD: "Leveraged cutting-edge technologies to drive transformative operational efficiencies"
- BANNED WORDS (using ANY of these = validation failure, do not use them even once):
  {banned_str}
- No em dashes. Use commas, periods, or hyphens.
- {AU_STYLE}

## HARD RULES
- Do NOT invent work, companies, roles, degrees, certifications or skills
- Do NOT invent or change numbers. Real numbers: {metrics_str}
- Preserved companies: {companies_str} -- names stay as-is
- Preserved projects: {projects_str}
- Preserved school: {school}"""


def _resume_shape(profile: dict) -> str:
    """Example of the resume JSON object the LLM must return."""
    school = profile.get("resume_facts", {}).get("preserved_school", "")
    education_level = profile.get("experience", {}).get("education_level", "")
    return (
        '{"title":"Role Title","summary":"2-3 tailored sentences.","skills":{"Languages":"...",'
        '"Frameworks":"...","DevOps & Infra":"...","Databases":"...","Tools":"..."},'
        '"experience":[{"header":"Title at Company","subtitle":"Tech | Dates",'
        '"bullets":["bullet 1","bullet 2","bullet 3","bullet 4"]}],'
        '"projects":[{"header":"Project Name - Description","subtitle":"Tech | Dates",'
        '"bullets":["bullet 1","bullet 2"]}],'
        f'"education":"{school} | {education_level}"}}'
    )


def _build_tailor_prompt(profile: dict) -> str:
    """Resume-only tailoring prompt (rules + output format)."""
    return (_tailor_rules(profile)
            + '\n\n## OUTPUT: Return ONLY valid JSON. No markdown fences. No commentary. No "here is" preamble.\n\n'
            + _resume_shape(profile))


def _build_application_prompt(profile: dict) -> str:
    """One request for both the tailored resume and the cover letter.

    They share the same context (resume, job, profile), so generating them
    together costs one LLM request instead of two or more.
    """
    return (
        _tailor_rules(profile)
        + "\n\n## COVER LETTER\nAlso write a cover letter for this job, consistent with the tailored resume.\n\n"
        + cover_letter_rules(profile)
        + "\n\n## OUTPUT: Return ONLY a JSON object with exactly two keys:\n"
        + '{"resume": <the tailored resume object>, '
        + '"cover_letter": "<the full letter text, paragraphs separated by \\n\\n>"}\n\n'
        + "The resume object has this shape:\n"
        + _resume_shape(profile)
    )


def _build_judge_prompt(profile: dict) -> str:
    """Build the LLM judge prompt from the user's profile."""
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Flatten allowed skills for the judge
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) if all_skills else "N/A"

    real_metrics = resume_facts.get("real_metrics", [])
    metrics_str = ", ".join(real_metrics) if real_metrics else "only those in the original resume"

    return f"""You are a resume quality judge. A tailoring engine rewrote a resume to target a specific job. Your job is to catch LIES, not style changes.

Answer with ONLY a JSON object: {{"verdict": "PASS" or "FAIL", "issues": "<problems found, or none>"}}
If a cover letter is included, apply the same rules to it: it may only claim work, tools and numbers from the original resume.

## CONTEXT -- what the tailoring engine was instructed to do (all of this is ALLOWED):
- Change the title to match the target role
- Rewrite the summary from scratch for the target job
- Reorder bullets and projects to put the most relevant first
- Reframe bullets to use the job's language
- Drop low-relevance bullets and replace with more relevant ones from other sections
- Reorder the skills section to put job-relevant skills first
- Change tone and wording extensively

## WHAT IS FABRICATION (FAIL for these):
1. Adding tools, languages, frameworks or certifications (in skills, bullets, summary or cover letter)
   that aren't in the original. The allowed skills are ONLY: {skills_str}
2. Inventing NEW metrics or numbers not in the original. The real metrics are: {metrics_str}
3. Inventing work that has no basis in any original bullet (completely new achievements).
4. Adding companies, roles, or degrees that don't exist.
5. Changing real numbers (inflating 80% to 95%, 500 nodes to 1000 nodes).

## WHAT IS NOT FABRICATION (do NOT fail for these):
- Rewording any bullet, even heavily, as long as the underlying work is real
- Combining two original bullets into one
- Splitting one original bullet into two
- Describing the same work with different emphasis
- Dropping bullets entirely
- Reordering anything
- Changing the title or summary completely

## JUDGEMENT
- Wording, emphasis and structure are free: never fail for style, tone, reordering or regrouping.
- A tool, language, framework, certification or number that isn't in the original resume or the allowed
  skills list is fabrication, even if it's closely related or easy to learn: an interviewer will ask about it.
- Using the job's term for the same real work ("REST APIs" for "web services") is fine.
- When in doubt about a reworded bullet, PASS if the same underlying work appears in the original."""


# ── JSON Extraction ───────────────────────────────────────────────────────

def extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response (handles fences, preamble).

    Args:
        raw: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    raw = raw.strip()

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Markdown fences
    if "```" in raw:
        for part in raw.split("```")[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    # Find outermost { ... }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON found in LLM response")


# ── Resume Assembly (profile-driven header) ──────────────────────────────

def assemble_resume_text(data: dict, profile: dict) -> str:
    """Convert JSON resume data to formatted plain text.

    Header (name, location, contact) is ALWAYS code-injected from the profile,
    never LLM-generated. All text fields are sanitized.

    Args:
        data: Parsed JSON resume from the LLM.
        profile: User profile dict from load_profile().

    Returns:
        Formatted resume text.
    """
    personal = profile.get("personal", {})
    lines: list[str] = []

    # Header -- always code-injected from profile
    lines.append(personal.get("full_name", ""))
    lines.append(sanitize_text(data.get("title", "Software Engineer")))

    # Location from search config or profile -- leave blank if not available
    # The location line is optional; the original used a hardcoded city.
    # We omit it here; the LLM prompt can include it if the user sets it.

    # Contact line
    contact_parts: list[str] = []
    if personal.get("email"):
        contact_parts.append(personal["email"])
    if personal.get("phone"):
        contact_parts.append(personal["phone"])
    if personal.get("github_url"):
        contact_parts.append(personal["github_url"])
    if personal.get("linkedin_url"):
        contact_parts.append(personal["linkedin_url"])
    if contact_parts:
        lines.append(" | ".join(contact_parts))
    lines.append("")

    # Summary
    lines.append("SUMMARY")
    lines.append(sanitize_text(data["summary"]))
    lines.append("")

    # Technical Skills
    lines.append("TECHNICAL SKILLS")
    if isinstance(data["skills"], dict):
        for cat, val in data["skills"].items():
            lines.append(f"{cat}: {sanitize_text(str(val))}")
    lines.append("")

    # Experience
    lines.append("EXPERIENCE")
    for entry in data.get("experience", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            lines.append(f"- {sanitize_text(b)}")
        lines.append("")

    # Projects
    lines.append("PROJECTS")
    for entry in data.get("projects", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            lines.append(f"- {sanitize_text(b)}")
        lines.append("")

    # Education
    lines.append("EDUCATION")
    lines.append(sanitize_text(str(data.get("education", ""))))

    return "\n".join(lines)


# ── LLM Judge ────────────────────────────────────────────────────────────

def judge_tailored_resume(
    original_text: str, tailored_text: str, job_title: str, profile: dict,
    cover_letter: str = "",
) -> dict:
    """LLM judge layer: catches subtle fabrication that programmatic checks miss.

    Args:
        original_text: Base resume text.
        tailored_text: Tailored resume text.
        job_title: Target job title.
        profile: User profile for building the judge prompt.
        cover_letter: Cover letter written alongside the resume, if any.

    Returns:
        {"passed": bool, "verdict": str, "issues": str}
    """
    letter_part = f"\n\n---\n\nCOVER LETTER:\n{cover_letter}" if cover_letter else ""
    messages = [
        {"role": "system", "content": _build_judge_prompt(profile)},
        {"role": "user", "content": (
            f"JOB TITLE: {job_title}\n\n"
            f"ORIGINAL RESUME:\n{original_text}\n\n---\n\n"
            f"TAILORED RESUME:\n{tailored_text}{letter_part}\n\n"
            "Judge it:"
        )},
    ]
    try:
        data = get_client().chat_json(messages, max_tokens=512, temperature=0.1)
    except ValueError:
        data = {}
    verdict = str(data.get("verdict", "")).strip().upper() if isinstance(data, dict) else ""
    passed = verdict == "PASS"
    issues = str(data.get("issues") or "none") if isinstance(data, dict) else "unreadable judge reply"
    return {"passed": passed, "verdict": verdict or "UNREADABLE", "issues": issues}


def _generated_text(resume: dict, letter: str) -> str:
    """The LLM-written prose (not the code-injected header), for number checks."""
    parts = [str(resume.get("title", "")), str(resume.get("summary", ""))]
    for section in ("experience", "projects"):
        for entry in resume.get(section) or []:
            if isinstance(entry, dict):
                parts += [str(entry.get("header", "")), str(entry.get("subtitle", ""))]
                parts += [str(b) for b in entry.get("bullets", [])]
    if isinstance(resume.get("skills"), dict):
        parts += [str(v) for v in resume["skills"].values()]
    parts.append(letter or "")
    return "\n".join(parts)


def judge_reasons(resume_text: str, resume: dict, letter: str, mode: str) -> list[str]:
    """Why the LLM fact-check should run; empty = the free checks found nothing.

    strict always runs it and lenient never does. In normal mode it runs only
    when code spots a likely fabrication anywhere in the resume or letter:
    numbers that aren't in the original resume (invented metrics), or tools
    from the fabrication watchlist that the original never mentions. Banned
    buzzwords are a style problem and don't need the judge.
    """
    if mode == "lenient":
        return []
    if mode == "strict":
        return ["strict mode"]
    generated = _generated_text(resume, letter)
    reasons = []
    invented = new_numbers(resume_text, generated)
    if invented:
        reasons.append(f"numbers not in your resume: {', '.join(invented[:5])}")
    original_lower, generated_lower = resume_text.lower(), generated.lower()
    tools = sorted(t for t in FABRICATION_WATCHLIST if len(t) > 2
                   and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", generated_lower)
                   and not re.search(rf"(?<!\w){re.escape(t)}(?!\w)", original_lower))
    if tools:
        reasons.append(f"tools not in your resume: {', '.join(tools[:5])}")
    return reasons


# ── Core Tailoring ───────────────────────────────────────────────────────

def tailor_application(
    resume_text: str, job: dict, profile: dict,
    max_retries: int = 3, validation_mode: str = "normal",
) -> tuple[str, str, dict]:
    """Tailored resume AND cover letter for one job, usually in one LLM request.

    - One JSON request returns both; code checks validate each.
    - The LLM judge only runs when judge_reasons() finds something (or in
      strict mode), instead of after every attempt.
    - A bad resume regenerates both; a bad cover letter alone gets a
      letter-only retry (generate_cover_letter).
    - Each retry is a fresh conversation with the previous issues listed.

    Returns:
        (tailored_text, cover_letter, report). report["requests"] counts
        LLM requests used; report["status"] is "approved",
        "approved_with_judge_warning", "failed_validation" or
        "exhausted_retries".
    """
    report: dict = {
        "attempts": 0, "requests": 0, "validator": None, "cover_validator": None,
        "judge": None, "judge_reasons": [], "status": "pending", "validation_mode": validation_mode,
    }
    client = get_client()
    base_prompt = _build_application_prompt(profile)
    target = job_text(job)
    avoid_notes: list[str] = []
    tailored, letter, letter_ok = "", "", False

    for attempt in range(max_retries + 1):
        report["attempts"] = attempt + 1
        prompt = base_prompt
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES (from previous attempt):\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"ORIGINAL RESUME:\n{resume_text}\n\n---\n\nTARGET JOB:\n{target}\n\n"
                                        "Return the JSON:"},
        ]
        report["requests"] += 1
        try:
            data = client.chat_json(messages, max_tokens=3072, temperature=0.4)
        except ValueError:
            avoid_notes.append("Output was not valid JSON. Return ONLY a JSON object, nothing else.")
            continue
        resume = data.get("resume") if isinstance(data, dict) else None
        if not isinstance(resume, dict):
            avoid_notes.append('Return a JSON object with "resume" and "cover_letter" keys.')
            continue

        # Cover letter: keep the best one seen (a valid one wins)
        raw_letter = data.get("cover_letter")
        if isinstance(raw_letter, str) and raw_letter.strip() and not letter_ok:
            candidate = clean_letter(raw_letter)
            cover_check = validate_cover_letter(candidate, mode=validation_mode)
            report["cover_validator"] = cover_check
            letter, letter_ok = candidate, cover_check["passed"]

        validation = validate_json_fields(resume, profile, mode=validation_mode)
        report["validator"] = validation
        if not validation["passed"]:
            avoid_notes.extend(validation["errors"])
            if attempt < max_retries:
                continue
            tailored = assemble_resume_text(resume, profile)
            report["status"] = "failed_validation"
            return tailored, letter, report

        tailored = assemble_resume_text(resume, profile)
        reasons = judge_reasons(resume_text, resume, letter, validation_mode)
        report["judge_reasons"] = reasons
        if not reasons:
            report["judge"] = {"verdict": "SKIPPED", "passed": True, "issues": "code checks clean"}
            report["status"] = "approved"
            break

        report["requests"] += 1
        judge = judge_tailored_resume(resume_text, tailored, job.get("title", ""), profile, letter)
        report["judge"] = judge
        if judge["passed"]:
            report["status"] = "approved"
            break
        avoid_notes.append(f"Judge rejected: {judge['issues']}")
        letter_ok = False  # the judge saw the letter too; regenerate it with the resume
        if attempt < max_retries:
            continue
        report["status"] = "approved_with_judge_warning"
        break
    else:
        report["status"] = "exhausted_retries"
        return tailored, letter, report

    if not letter_ok:
        # Resume is fine; only the letter needs another go (plain-text request)
        stats = {"requests": 0}
        letter = generate_cover_letter(tailored, job, profile, max_retries=1,
                                       validation_mode=validation_mode, stats=stats)
        report["requests"] += stats["requests"]
    return tailored, letter, report


# ── Batch Entry Point ────────────────────────────────────────────────────

def run_tailoring(min_score: int = 7, limit: int = 20,
                  validation_mode: str = "normal") -> dict:
    """Generate tailored resumes and cover letters for high-scoring jobs.

    Each job's resume and cover letter come from one LLM request (see
    tailor_application), so the separate cover-letter stage only has work
    for jobs tailored before this change.

    Args:
        min_score:       Minimum fit_score to tailor for.
        limit:           Maximum jobs to process.
        validation_mode: "strict", "normal", or "lenient".

    Returns:
        {"approved": int, "failed": int, "errors": int, "cover_letters": int,
         "requests": int, "elapsed": float}
    """
    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()
    mark_duplicates(conn)  # skip roles already found on another site

    jobs = get_jobs_by_stage(conn=conn, stage="pending_tailor", min_score=min_score, limit=limit)

    if not jobs:
        log.info("No untailored jobs with score >= %d.", min_score)
        return {"approved": 0, "failed": 0, "errors": 0, "cover_letters": 0, "requests": 0, "elapsed": 0.0}

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    total_requests = 0
    log.info("Tailoring resumes for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    stats: dict[str, int] = {"approved": 0, "failed_validation": 0, "failed_judge": 0, "error": 0}

    for job in jobs:
        completed += 1
        try:
            tailored, letter, report = tailor_application(resume_text, job, profile,
                                                          validation_mode=validation_mode)
            total_requests += report["requests"]

            # Build safe filename prefix
            safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50].strip().replace(" ", "_")
            safe_site = re.sub(r"[^\w\s-]", "", job.get("company") or job["site"])[:20].strip().replace(" ", "_")
            prefix = f"{safe_site}_{safe_title}"

            # Save tailored resume text
            txt_path = TAILORED_DIR / f"{prefix}.txt"
            txt_path.write_text(tailored, encoding="utf-8")

            # Save job description for traceability
            job_path = TAILORED_DIR / f"{prefix}_JOB.txt"
            job_desc = (
                f"Title: {job['title']}\n"
                f"Company: {job['site']}\n"
                f"Location: {job.get('location', 'N/A')}\n"
                f"Score: {job.get('fit_score', 'N/A')}\n"
                f"URL: {job['url']}\n\n"
                f"{job.get('full_description', '')}"
            )
            job_path.write_text(job_desc, encoding="utf-8")

            # Save validation report
            report_path = TAILORED_DIR / f"{prefix}_REPORT.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Generate PDF for approved resumes (best-effort)
            # "approved_with_judge_warning" is also a success — resume was generated.
            pdf_path = None
            cl_path = None
            if report["status"] in ("approved", "approved_with_judge_warning"):
                try:
                    from applypilot.scoring.pdf import convert_to_pdf
                    pdf_path = str(convert_to_pdf(txt_path))
                except Exception:
                    log.debug("PDF generation failed for %s", txt_path, exc_info=True)
                # Cover letter from the same request
                if letter:
                    cl_file = COVER_LETTER_DIR / f"{prefix}_CL.txt"
                    cl_file.write_text(letter, encoding="utf-8")
                    cl_path = str(cl_file)
                    try:
                        from applypilot.scoring.pdf import convert_to_pdf
                        convert_to_pdf(cl_file)
                    except Exception:
                        log.debug("PDF generation failed for %s", cl_file, exc_info=True)

            result = {
                "url": job["url"],
                "path": str(txt_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
                "status": report["status"],
                "attempts": report["attempts"],
                "requests": report["requests"],
                "cover_letter_path": cl_path,
            }
        except BudgetExhausted as e:
            # Not this job's fault: leave it (and the rest) untouched for the next run
            log.warning("%s Stopping: %d jobs left for the next run.", e, len(jobs) - completed + 1)
            break
        except Exception as e:
            result = {
                "url": job["url"], "title": job["title"], "site": job["site"],
                "status": "error", "attempts": 0, "path": None, "pdf_path": None,
            }
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

        results.append(result)
        stats[result.get("status", "error")] = stats.get(result.get("status", "error"), 0) + 1

        elapsed = time.time() - t0
        rate = completed / elapsed if elapsed > 0 else 0
        log.info(
            "%d/%d [%s] attempts=%s requests=%s | %.1f jobs/min | %s",
            completed, len(jobs),
            result["status"].upper(),
            result.get("attempts", "?"),
            result.get("requests", "?"),
            rate * 60,
            result["title"][:40],
        )

    # Persist to DB: increment attempt counter for ALL, save path only for approved
    now = datetime.now(timezone.utc).isoformat()
    _success_statuses = {"approved", "approved_with_judge_warning"}
    for r in results:
        if r["status"] in _success_statuses:
            conn.execute(
                "UPDATE jobs SET tailored_resume_path=?, tailored_at=?, "
                "tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
            if r.get("cover_letter_path"):
                conn.execute(
                    "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, "
                    "cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                    (r["cover_letter_path"], now, r["url"]),
                )
        else:
            conn.execute(
                "UPDATE jobs SET tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    cover_letters = sum(1 for r in results if r.get("cover_letter_path"))
    log.info(
        "Tailoring done in %.1fs: %d approved, %d failed_validation, %d failed_judge, %d errors, "
        "%d cover letters, %d LLM requests",
        elapsed,
        stats.get("approved", 0),
        stats.get("failed_validation", 0),
        stats.get("failed_judge", 0),
        stats.get("error", 0),
        cover_letters,
        total_requests,
    )

    return {
        "approved": stats.get("approved", 0),
        "failed": stats.get("failed_validation", 0) + stats.get("failed_judge", 0),
        "errors": stats.get("error", 0),
        "cover_letters": cover_letters,
        "requests": total_requests,
        "elapsed": elapsed,
    }

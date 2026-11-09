"""Recruiter screen (simulated): how an employer's AI screener might rate a resume for a job.

The fit score asks whether a job suits the candidate (pay, location, work type, visas). This asks the
other side's question: reading only the job ad and the resume, would a screener move this applicant to
the next round? It's run on the base resume and on the tailored one, so you can see whether tailoring
helped and which must-haves the resume still doesn't show.

It only reports. Nothing here feeds back into tailoring, on purpose: a writer told to raise a screen
score starts claiming things. A gap it finds is a real gap, and the fact-check keeps tailoring from
inventing over it. Real screeners vary (keyword ranking, an LLM, a person skimming), so this is an
estimate, not a prediction.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from applypilot.llm import get_client

log = logging.getLogger(__name__)

SCREEN_PROMPT = """You are the AI resume screener an employer uses for first-round screening. A recruiter
will read your rating to decide which applicants go to the next round. You see only the job ad and one
applicant's resume. You know nothing else about the applicant: not their salary expectations or
preferences, so don't guess at them.

Judge how well the resume shows what the ad asks for:
- The ad's must-haves first: is each one shown? Experience that demonstrates it (what they did, at what
  scale, with what result) is strong evidence. A word in a skills list with nothing behind it in the
  experience is weak evidence. A must-have that appears nowhere is not shown.
- Then the role itself: same kind of work and seniority as the ad, relevant industry, recent experience.
- Nice-to-haves only move a close call.
- Keyword stuffing, vague claims and buzzwords don't earn credit; specific, believable results do.
- Location counts only if the ad is onsite somewhere and the resume clearly places the applicant far away.
- If the ad requires citizenship, a security clearance or work rights and the resume doesn't state them,
  list them as not shown, but don't lower the score for it: that's checked later in the process.

Score 1-10, how likely you'd send this applicant to the next round:
- 9-10: shows every must-have convincingly; a clear yes.
- 7-8: shows the must-haves; a likely yes.
- 5-6: a maybe: close, but a must-have is weak or missing, or the seniority is a stretch. Any must-have
  the resume doesn't show at all keeps the score at 6 or below.
- 3-4: unlikely: several must-haves not shown, or a different kind of role.
- 1-2: no: a different field, or far off on seniority.

Return JSON only:
{"score": <1-10>,
 "not_shown": ["each must-have the resume doesn't show, as a short term in the ad's own words (only the
               ad's essentials, never its nice-to-haves)"],
 "noticed": ["2 or 3 short points a recruiter would notice first, good or bad, each under 20 words"]}"""


def verdict(score: int) -> str:
    """advance / maybe / reject, fixed by the score so the badge and the number always agree."""
    return "advance" if score >= 7 else "maybe" if score >= 5 else "reject"


def _strings(value, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    if not isinstance(value, list):
        return []
    out = [" ".join(str(v).split()) for v in value]
    return [v[:200] for v in out if v][:limit]


def fingerprint(text: str) -> str:
    """A short hash of the resume screened, so a later edit shows the screen is out of date."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:12]


def screen(resume_text: str, job: dict) -> dict:
    """One screening request: {"score", "verdict", "not_shown", "noticed"}. Raises on a bad reply."""
    ad = (job.get("full_description") or job.get("description") or "").strip()
    head = " | ".join(x for x in (job.get("title"), job.get("company"), job.get("location")) if x)
    messages = [
        {"role": "system", "content": SCREEN_PROMPT},
        {"role": "user", "content": f"JOB AD: {head}\n{ad}\n\n---\n\nRESUME:\n{resume_text.strip()}"},
    ]
    data = get_client().chat_json(messages, temperature=0.0, max_tokens=700)
    try:
        score = int(data.get("score"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("The screener's reply had no score") from None
    if not 1 <= score <= 10:
        raise ValueError(f"The screener's score was out of range: {score}")
    return {"score": score, "verdict": verdict(score), "not_shown": _strings(data.get("not_shown"), 12),
            "noticed": _strings(data.get("noticed"), 3)}


def run(job: dict, base_resume: str, job_resume: str, own: bool = False) -> dict:
    """Screen the base resume and the one this job uses (two requests). The stored report."""
    if not (job.get("full_description") or job.get("description")):
        raise ValueError("This job has no ad text to screen against: get the ad again first")
    if not job_resume.strip():
        raise ValueError("No text could be read from this job's resume")
    report = {"when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "model": getattr(get_client(), "model", "") or "", "own": own,
              "resume_hash": fingerprint(job_resume), "base": None,
              "tailored": screen(job_resume, job)}
    if base_resume.strip():
        report["base"] = screen(base_resume, job)
    return report

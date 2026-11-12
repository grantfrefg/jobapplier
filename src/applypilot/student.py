"""Student visas: what work fits while studying, and after the course ends.

One place for the rules, used by the scorer (candidate facts), the form filler and the Claude
Code / Codex prompt, so they never disagree. Rules as published in late 2026 (check the official
pages when they change):

- Australia, Student visa (subclass 500), condition 8105: up to 48 hours a fortnight while the
  course is in session, unlimited during official breaks. Research Master's and PhD students:
  no limit once the course has started. After the course, the Temporary Graduate visa
  (subclass 485) gives full work rights.
  https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/student-500
- UK, Student visa: degree-level students up to 20 hours a week in term time (10 below degree
  level), full-time during official vacations; a student can't fill a permanent full-time
  vacancy. After the course, the Graduate visa gives an unrestricted right to work (2 years if
  applied for by 31 December 2026, 18 months from 1 January 2027; 3 years after a PhD).
  https://www.gov.uk/student-visa/work and https://www.gov.uk/graduate-visa

The profile says when the course ends (work_authorization.course_end, MM/YYYY), what level it is
(study_level) and which visa follows it (post_study_visa).
"""

from __future__ import annotations

import re
from datetime import date

# How long before the course ends graduate roles (starting after it) count as a fit
FINISHING_DAYS = 120

STUDY_LEVELS = {
    "au": ["", "Bachelor's or coursework Master's", "Research Master's or PhD", "Vocational (VET) or English course"],
    "uk": ["", "Degree level", "Below degree level"],
}
POST_STUDY = {
    "au": ["", "Temporary Graduate visa (subclass 485)", "None or not sure"],
    "uk": ["", "Graduate visa", "None or not sure"],
}

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}


def _auth(profile: dict | None) -> dict:
    return (profile or {}).get("work_authorization") or {}


def is_student(profile: dict | None) -> bool:
    return "student" in str(_auth(profile).get("work_permit_type") or "").lower()


def course_end(profile: dict | None) -> date | None:
    """The last day of the course's final month ("11/2026", "2026-11", "Nov 2026"), or None."""
    text = str(_auth(profile).get("course_end") or "").strip().lower()
    month = year = None
    if m := re.fullmatch(r"(\d{1,2})\s*[/.-]\s*(\d{4})", text):
        month, year = int(m.group(1)), int(m.group(2))
    elif m := re.fullmatch(r"(\d{4})\s*[/.-]\s*(\d{1,2})", text):
        year, month = int(m.group(1)), int(m.group(2))
    elif m := re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", text):
        month, year = _MONTHS.get(m.group(1)), int(m.group(2))
    if not month or not 1 <= month <= 12 or not year:
        return None
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def post_study_visa(profile: dict | None) -> str:
    value = str(_auth(profile).get("post_study_visa") or "").strip()
    return "" if value.lower().startswith("none") else value


def stage(profile: dict | None, today: date | None = None) -> str:
    """"" (not a student), "studying", "finishing" (within FINISHING_DAYS of the end) or "graduated"."""
    if not is_student(profile):
        return ""
    end, today = course_end(profile), today or date.today()
    if end is None:
        return "studying"
    if today > end:
        return "graduated"
    return "finishing" if (end - today).days <= FINISHING_DAYS else "studying"


def _research(profile: dict) -> bool:
    return "research" in str(_auth(profile).get("study_level") or "").lower()


def hours_limit(profile: dict, mk) -> str:
    """The work limit while the course is in session, in words."""
    if getattr(mk, "key", "au") == "uk":
        below = "below" in str(_auth(profile).get("study_level") or "").lower()
        return (f"up to {10 if below else 20} hours a week in term time, full-time during official "
                "vacations")
    if _research(profile):
        return "no hours limit (research Master's or PhD)"
    return "up to 48 hours a fortnight while the course is in session, unlimited during official breaks"


def _end_text(profile: dict) -> str:
    end = course_end(profile)
    return f"{end:%B %Y}" if end else ""


def scoring_lines(profile: dict, mk, today: date | None = None) -> list[str]:
    """Candidate facts for the scorer: which roles fit this student's work rights now."""
    st = stage(profile, today)
    if not st:
        return []
    uk = getattr(mk, "key", "au") == "uk"
    today = today or date.today()
    after = post_study_visa(profile)
    end = _end_text(profile)
    if st == "graduated":
        if after:
            rights = ("an unrestricted right to work in the UK for 18 months to 2 years, no sponsorship needed"
                      if uk else "full work rights for 2 years or more, any employer, no sponsorship needed")
            return [f"Course finished ({end}); now on a {after}: {rights}. Full-time and permanent roles fit; "
                    "an ad requiring citizenship" + (" or settled status" if uk else " or permanent residency")
                    + " is a work-rights dealbreaker."]
        return [f"Course finished ({end}) and no visa after it is stated: their right to work now is unclear. "
                "Don't treat full-time roles as a fit; part-time and casual roles are the safest."]
    if _research(profile) and not uk:
        lines = [f"Student visa (research Master's or PhD): no work-hours limit. Today is {today:%B %Y}."]
    else:
        lines = [f"Student visa: {hours_limit(profile, mk)}. Today is {today:%B %Y}."]
        breaks = "June-July and November-February" if not uk else "the summer, Christmas and Easter vacations"
        lines.append(
            "Part-time, casual and internship roles fit best, and full-time vacation or summer programs that "
            f"fall in official breaks ({breaks}) fit too. "
            + ("A permanent full-time role is a work-rights dealbreaker: a student can't fill a permanent "
               "full-time vacancy (score 3 or lower and name it)"
               if uk else "A full-time role during study periods mostly doesn't fit the hours limit: score it 4 at "
                          "most")
            + ", unless the ad offers part-time or flexible hours, or it starts after the course ends"
            + (f" ({end})" if end else "") + " and a visa with full work rights follows it.")
    if st == "finishing" or end:
        if after:
            lines.append(f"Course ends {end}; then a {after} gives full work rights. Graduate programs and roles "
                         f"starting after {end} fit if the employer accepts temporary visa holders.")
        elif end:
            lines.append(f"Course ends {end}; no visa after it is stated, so roles starting after that date "
                         "don't fit unless the ad offers sponsorship.")
    lines.append("Graduate programs, government and defence roles that require "
                 + ("British citizenship or settled status" if uk else "citizenship or permanent residency")
                 + " are a work-rights dealbreaker.")
    return lines


def form_lines(profile: dict, mk, today: date | None = None) -> list[str]:
    """Exact answers for work-rights questions on application forms."""
    st = stage(profile, today)
    if not st:
        return []
    uk = getattr(mk, "key", "au") == "uk"
    after = post_study_visa(profile)
    end = _end_text(profile)
    if st == "graduated" and after:
        return [f"Work rights: {after} (course finished {end}). "
                + ("Right to work in the UK: yes, no sponsorship needed now; the visa is temporary."
                   if uk else "Full work rights: yes, no sponsorship needed now; the visa is temporary, so "
                              "'permanent resident or citizen?' is no.")]
    lines = ["Work rights: Student visa" + ("" if uk else " (subclass 500)") + ". "
             + ("Unrestricted / full work rights: no. " if not (_research(profile) and not uk) else "")
             + f"Hours: {hours_limit(profile, mk)}."]
    if end:
        lines.append(f"Course ends {end}"
                     + (f"; after that a {after} (full work rights) - mention it only when a question asks "
                        "about availability or work rights after study." if after else "."))
    lines.append("'Available full-time?' during study periods: no (or the part-time option); during "
                 "official breaks or after the course ends with full work rights: yes. Never say they're a "
                 "citizen or permanent resident.")
    return lines

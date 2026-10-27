"""ATS check for a tailored resume, tuned for Australian employers. No AI requests.

Australian employers mostly screen through an applicant tracking system:
Workday and SAP SuccessFactors (banks, miners, large retailers), PageUp
(government, universities), plus JobAdder, Greenhouse, Lever, iCIMS and
SmartRecruiters; SEEK applications are usually passed on into the
employer's own ATS. None of them publish their rules, but they work the
same way: the resume is parsed into fields (contact details, job titles,
employers, dates, skills), recruiters filter on knockout facts (work
rights, location) and search or rank by the job ad's words.

This mirrors those steps on the resume the app wrote:

- Parsing: standard section headings, contact details and location in the
  header, each job with a title, employer and date range, a sensible length.
- Knockouts: work rights stated, location stated, no personal details
  Australian employers shouldn't receive (date of birth, photo).
- Keywords: the tools and skills the ad names that the resume contains, in
  the ad's own words, and the job title's main words.
- Australian English spelling.

The score is a guide to how well a parser and a keyword search will see
the resume, not a prediction from any one vendor.
"""

from __future__ import annotations

import re

from applypilot.scoring import facts

_DATE_RANGE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}\s*[-–to]+\s*"
    r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|present|current|now)\b"
    r"|\b\d{4}\s*[-–]\s*(?:\d{4}|present|current)\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+61|\(0\d\)|0)[\d\s()-]{8,}")
_AU_STATES = re.compile(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT|New South Wales|Victoria|Queensland|Tasmania|"
                        r"Australian Capital Territory|Northern Territory|Western Australia|South Australia)\b")
_WORK_RIGHTS = re.compile(r"citizen|permanent resident|working rights|work rights|visa|right to work", re.I)
_TITLE_STOP = {"senior", "junior", "lead", "principal", "staff", "graduate", "level", "and", "the", "of",
               "australia", "sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra", "remote",
               "hybrid", "contract", "permanent", "full", "time", "part"}
WORDS_PER_PAGE = 550


def _has(term: str, text_norm: str) -> bool:
    t = facts.normalize(term).strip()
    return bool(t) and re.search(rf"(?<![a-z0-9]){re.escape(t)}(?:e?s)?(?![a-z0-9])", text_norm) is not None


def ad_terms(job: dict | None) -> list[str]:
    """Tools, platforms and skills named in the job ad, plus the scorer's must-haves."""
    ad = facts.normalize(" ".join(str((job or {}).get(k) or "") for k in ("title", "full_description",
                                                                          "description")))
    terms = [t for t in facts.TECH_TERMS if _has(t, ad) and t not in ("certified", "certification", "certificate")]
    first = str((job or {}).get("score_reasoning") or "").split("\n", 1)[0]
    terms += [k.strip(" []()\"'.") for k in first.split(",") if 1 < len(k.strip(" []()\"'.")) <= 40]
    seen, out = set(), []
    for t in terms:
        if t.lower() not in seen and not any(t.lower() in o.lower() and t.lower() != o.lower() for o in terms):
            seen.add(t.lower())
            out.append(t)
    return out


def check(resume_text: str, job: dict | None = None, original_text: str = "") -> dict:
    """ATS report: {"score": 0-100, "keyword_match": %, "matched", "missing_have", "missing_gap",
    "issues": [...], "passed": [...]}."""
    from applypilot.scoring.pdf import parse_resume, parse_entries
    from applypilot.scoring.validator import _AU_SPELLING

    issues: list[str] = []
    passed: list[str] = []
    parsed = parse_resume(resume_text or "")
    sections = {k.upper(): v for k, v in parsed["sections"].items()}
    header = " ".join([parsed.get("location", ""), parsed.get("contact", "")])

    # -- Parsing ---------------------------------------------------------------
    for name, keys in (("Summary", ("SUMMARY",)), ("Skills", ("TECHNICAL SKILLS", "SKILLS")),
                       ("Experience", ("EXPERIENCE",)), ("Education", ("EDUCATION",))):
        if any(sections.get(k, "").strip() for k in keys):
            passed.append(f"'{name}' section with a standard heading")
        else:
            issues.append(f"No '{name}' section: parsers look for that heading")
    (passed if _EMAIL.search(header) else issues).append(
        "Email in the header" if _EMAIL.search(header) else "No email address in the header")
    (passed if _PHONE.search(header) else issues).append(
        "Phone number in the header" if _PHONE.search(header) else "No phone number in the header")
    entries = parse_entries(sections.get("EXPERIENCE", ""))
    undated = [e["title"] for e in entries if not _DATE_RANGE.search(f"{e.get('title', '')} {e.get('subtitle', '')}")]
    if entries and not undated:
        passed.append("Every job has a title, employer and date range")
    elif undated:
        issues.append(f"No date range a parser can read for: {', '.join(t[:40] for t in undated[:3])} "
                      "(use 'Mar 2021 - Present')")
    words = len((resume_text or "").split())
    pages = round(words / WORDS_PER_PAGE, 1)
    if words < 250:
        issues.append(f"Very short ({words} words): too little for a keyword search to find")
    elif pages > 3.2:
        issues.append(f"About {pages:g} pages: Australian resumes are normally 2, at most 3")
    else:
        passed.append(f"Length about {max(pages, 1):g} page(s)")

    # -- Knockout facts ----------------------------------------------------------
    if _WORK_RIGHTS.search(header):
        passed.append("Work rights stated (a hard filter for Australian employers)")
    else:
        issues.append("Work rights not stated in the header (e.g. 'Australian Citizen'): employers filter on it."
                      " Set it in Setup -> profile -> work_authorization")
    if _AU_STATES.search(header):
        passed.append("Location (suburb/city and state) stated")
    else:
        issues.append("No city and state in the header: recruiters filter by location")
    if re.search(r"date of birth|\bdob\b|marital status|\bphoto\b", resume_text or "", re.I):
        issues.append("Remove personal details (date of birth, marital status, photo): not used in Australia")

    # -- Keywords ----------------------------------------------------------------
    body = facts.normalize(resume_text)
    original = facts.Source(original_text) if original_text else None
    terms = ad_terms(job)
    matched = [t for t in terms if _has(t, body)]
    missing = [t for t in terms if t not in matched]
    missing_have = [t for t in missing if original and original.mentions(t)]
    missing_gap = [t for t in missing if t not in missing_have]
    # Gaps the candidate genuinely doesn't have can't be fixed honestly, so the
    # match counts what could be in the resume.
    possible = len(matched) + len(missing_have)
    keyword_match = round(100 * len(matched) / possible) if possible else 100
    if missing_have:
        issues.append(f"In your resume but missing here: {', '.join(missing_have[:8])}")
    if terms:
        passed.append(f"{len(matched)} of the ad's {len(terms)} key terms appear")
    title_words = [w for w in re.findall(r"[a-z]+", facts.normalize((job or {}).get("title", "")))
                   if w not in _TITLE_STOP and len(w) > 1]
    title_hits = [w for w in title_words if _has(w, body)]
    if title_words and len(title_hits) < len(title_words):
        issues.append("Some words of the job title don't appear: "
                      f"{', '.join(w for w in title_words if w not in title_hits)} (fine if you don't do that)")

    # -- Australian spelling ---------------------------------------------------
    american = sorted({m.group(0).lower() for m in re.finditer(
        r"\b(" + "|".join(map(re.escape, _AU_SPELLING)) + r")\b", resume_text or "", re.I)
        if not (original_text and re.search(rf"\b{re.escape(m.group(0))}\b", original_text, re.I))})
    if american:
        issues.append(f"American spelling: {', '.join(american[:5])}")
    else:
        passed.append("Australian English spelling")

    structural = len([i for i in issues if not i.startswith(("In your resume", "Some words", "American"))])
    score = max(0, min(100, round(0.6 * keyword_match + 40 - 8 * structural - (3 if american else 0))))
    return {"score": score, "keyword_match": keyword_match, "matched": matched, "missing_have": missing_have,
            "missing_gap": missing_gap, "issues": issues, "passed": passed}

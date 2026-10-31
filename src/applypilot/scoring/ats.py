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
  the ad's own words, and the job title's main words. Another form of the
  same term ("Amazon Web Services" for "AWS") counts half: some systems
  match it, but a recruiter's search for the ad's word often won't.
- Australian English spelling, and no characters a parser may misread.
- The PDF itself: its text is read back the way a parser would, to check
  the name, contact details, sections and every job's dates survive.

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


def check(resume_text: str, job: dict | None = None, original_text: str = "", pdf_path=None) -> dict:
    """ATS report: {"score": 0-100, "keyword_match": %, "matched", "other_form", "missing_have",
    "missing_gap", "issues": [...], "passed": [...]}.

    With `pdf_path`, the PDF's text is also read back and checked (pdf_readback).
    """
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
    from applypilot.scoring import look
    from applypilot.scoring.pdf import page_count
    words = len((resume_text or "").split())
    pages = round(words / WORDS_PER_PAGE, 1)
    target = look.pages_target()
    real = page_count(pdf_path) if pdf_path and str(pdf_path).lower().endswith(".pdf") else 0
    if words < (150 if target == 1 else 250):
        issues.append(f"Very short ({words} words): too little for a keyword search to find")
    elif target == 1 and (real or pages) > 1.15:
        issues.append(f"{f'{real} pages' if real else f'About {pages:g} pages'}: a graduate resume fits on one "
                      "page (Resume look -> Length). Trim the least relevant bullets, or Redo the resume")
    elif (real or pages) > 3.2:
        issues.append(f"About {real or pages:g} pages: Australian resumes are normally 2, at most 3")
    else:
        passed.append(f"{real} page(s)" if real else f"Length about {max(pages, 1):g} page(s)")

    # -- Knockout facts ----------------------------------------------------------
    from applypilot.scoring import look
    if _WORK_RIGHTS.search(header):
        passed.append("Work rights stated (a hard filter for Australian employers)")
    elif look.work_rights_left_off():
        passed.append("Work rights left off the resume (Setup -> Resume look); forms still ask, and the "
                      "apply step answers them")
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
    # The same thing in another form ("Amazon Web Services" for "AWS"): term -> the form used
    other_form = {t: alias for t in terms if t not in matched
                  for alias in sorted(facts.aliases_of(t), key=len, reverse=True) if _has(alias, body)}
    missing = [t for t in terms if t not in matched and t not in other_form]
    missing_have = [t for t in missing if original and original.mentions(t)]
    missing_gap = [t for t in missing if t not in missing_have]
    # Gaps the candidate genuinely doesn't have can't be fixed honestly, so the
    # match counts what could be in the resume. Another form counts half.
    possible = len(matched) + len(other_form) + len(missing_have)
    keyword_match = round(100 * (len(matched) + 0.5 * len(other_form)) / possible) if possible else 100
    if other_form:
        issues.append("Different wording from the ad (a search for the ad's word may miss it): " + ", ".join(
            f"'{t}' (here: '{a}')" for t, a in list(other_form.items())[:6]))
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

    odd = misread_characters(resume_text)
    if odd:
        issues.append(f"Characters a parser may misread: {', '.join(odd)} (retype them, or export the file again)")

    # -- The PDF itself -----------------------------------------------------------
    if pdf_path:
        pdf_issues, pdf_passed = pdf_readback(pdf_path, resume_text)
        issues += pdf_issues
        passed += pdf_passed

    structural = len([i for i in issues
                      if not i.startswith(("In your resume", "Some words", "American", "Different wording"))])
    score = max(0, min(100, round(0.6 * keyword_match + 40 - 8 * structural - (3 if american else 0))))
    return {"score": score, "keyword_match": keyword_match, "matched": matched, "other_form": other_form,
            "missing_have": missing_have, "missing_gap": missing_gap, "issues": issues, "passed": passed}


# Ligatures ("ﬁ" for "fi"), the replacement character, private-use glyphs
# (icon fonts) and unmapped font codes: a parser reads these as something else
# or nothing, so "ﬁnance" never matches "finance".
_MISREAD = re.compile(r"[\ufb00-\ufb06\ufffd\ue000-\uf8ff]|\(cid:\d+\)")


def misread_characters(text: str) -> list[str]:
    """Up to five such characters found in the text (ligatures shown as they are, others by code)."""
    found = []
    for m in _MISREAD.finditer(text or ""):
        c = m.group(0)
        label = c if len(c) > 1 or "\ufb00" <= c <= "\ufb06" else f"U+{ord(c):04X}"
        if label not in found:
            found.append(label)
    return found[:5]


def pdf_text(pdf_path) -> str:
    """The PDF's text as a parser extracts it ('' if it has none or can't be read)."""
    try:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages).strip()
    except Exception:
        return ""


def pdf_readback(pdf_path, resume_text: str) -> tuple[list[str], list[str]]:
    """Read the PDF back like a parser and check what the resume says survives.

    Returns (issues, passed). Checks: there is text at all; the name comes
    first; the email and phone are there; the sections appear in the same
    order; each job's title, employer and date range are found together.
    """
    from applypilot.scoring.pdf import parse_entries, parse_resume, split_entry

    text = pdf_text(pdf_path)
    if not text:
        return (["The PDF has no readable text (a scan or an image?): applicant tracking systems can't read it"],
                [])
    issues: list[str] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    flat = facts.normalize(text)
    parsed = parse_resume(resume_text or "")
    name = (parsed.get("name") or "").strip()
    if name and not facts.normalize(lines[0]).startswith(facts.normalize(name)):
        issues.append(f"PDF: your name isn't the first thing a parser reads (it reads '{lines[0][:40]}')")
    header = " ".join([parsed.get("contact", ""), parsed.get("location", "")])
    for label, pattern in (("email address", _EMAIL), ("phone number", _PHONE)):
        want = pattern.search(header)
        if want and facts.normalize(want.group(0)).strip() not in flat:
            issues.append(f"PDF: the {label} can't be read from the PDF")

    # Sections, in order
    order = [h.upper() for h in parsed["sections"] if parsed["sections"][h].strip() and h.upper() in _RENDERED]
    upper = [ln.upper() for ln in lines]
    found = [next((i for i, ln in enumerate(upper) if ln in _HEADINGS.get(h, {h})), -1) for h in order]
    missing = [h.title() for h, i in zip(order, found) if i < 0]
    if missing:
        issues.append(f"PDF: section heading(s) not found as their own line: {', '.join(missing)}")
    elif found != sorted(found):
        issues.append("PDF: sections come out in a different order than written")

    # Every job: title, employer and dates found together
    lost = []
    for entry in parse_entries(parsed["sections"].get("EXPERIENCE", "")):
        heading, _, _ = split_entry(entry.get("title", ""), entry.get("subtitle", ""))  # "Title, Employer"
        parts = [facts.normalize(x).strip() for x in heading.rsplit(", ", 1)]
        hit = False
        for i, ln in enumerate(lines):
            if all(x in facts.normalize(ln) for x in parts):
                hit = bool(_DATE_RANGE.search(ln) or (i + 1 < len(lines) and _DATE_RANGE.search(lines[i + 1])))
                break
        if not hit:
            lost.append(heading[:50])
    if lost:
        issues.append(f"PDF: a parser may not link these jobs to their dates: {'; '.join(lost[:3])}")

    passed = [] if issues else ["PDF reads back correctly (name, contact details, sections, job dates)"]
    return issues, passed


# A heading as written in the text file -> the headings the PDF may print for it
_HEADINGS = {"TECHNICAL SKILLS": {"TECHNICAL SKILLS", "SKILLS"}}
# The sections the resume PDF prints (pdf.build_html)
_RENDERED = {"SUMMARY", "TECHNICAL SKILLS", "SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}

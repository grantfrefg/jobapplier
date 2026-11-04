"""How the resume PDF looks (Setup -> Resume look), applied when the PDF is made.

- RESUME_LAYOUT: modern (default), classic, compact or traditional; mine follows the style
  of your uploaded resume PDF (style_match.py); word writes into your own Word file
  (docx_fill.py) and keeps a PDF copy in your PDF's style. All PDFs are one column of
  real text, so ATS read them the same way.
- RESUME_ACCENT: the one colour used for section headings (and the name in classic).
- RESUME_TAGLINE: "on" adds the AI's few words after your job title
  ("Senior Data Scientist · Production ML"). Off by default: it reads as AI-tailored.
- RESUME_TECH_LINE: "on" shows the tools line under each job. Off by default: the
  bullets and skills already name them.
- SHOW_WORK_RIGHTS: auto (default) shows citizenship, residency and full work rights,
  and a working holiday visa on resumes for contract, temp and casual roles (decided per
  job from its ad); for permanent or unclear roles, and for other limited visas such as
  student visas, it's left off; always; never.

Applied when the PDF is rendered, so changing one and running "PDF" again restyles
every resume without asking the AI.
"""

from __future__ import annotations

import os
import re

LAYOUTS = {"modern": "Modern", "classic": "Classic", "compact": "Compact", "traditional": "Traditional",
           "mine": "Match my resume", "word": "My Word file"}
OWN_STYLE = ("mine", "word")  # follow your own resume (the PDF's style; the Word file itself for "word")
ACCENTS = {"navy": "#1f3a5f", "teal": "#0f5e63", "green": "#2f5d3a", "burgundy": "#6b1f2a",
           "plum": "#4b2e5a", "charcoal": "#2b2b2b"}
WORK_RIGHTS_MODES = ("auto", "always", "never")
PAGE_MODES = ("auto", "1", "2")

_WORK_RIGHTS = re.compile(r"citizen|permanent resident|work(ing)? rights|visa|right to work", re.I)
_LIMITED = re.compile(r"working holiday|student|limited|bridging|visa", re.I)


def _on(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in ("on", "1", "true", "yes")


def layout() -> str:
    value = os.environ.get("RESUME_LAYOUT", "").strip().lower()
    return value if value in LAYOUTS else "modern"


def accent() -> str:
    value = os.environ.get("RESUME_ACCENT", "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    return ACCENTS.get(value, ACCENTS["navy"])


def tagline() -> bool:
    return _on("RESUME_TAGLINE")


def tech_line() -> bool:
    return _on("RESUME_TECH_LINE")


def work_rights_mode() -> str:
    value = os.environ.get("SHOW_WORK_RIGHTS", "").strip().lower()
    return value if value in WORK_RIGHTS_MODES else "auto"


# What makes an ad contract/temp/casual work, where a working holiday visa is worth stating
_SHORT_TERM = re.compile(
    r"\b(temp|temporary|casual|fixed[- ]term|locum|seasonal|harvest|day rate|daily rate|"
    r"(maternity|parental)( leave)? (cover|contract)|\d{1,2}[- ]?(month|week)s? (contract|role|assignment|"
    r"position|engagement|project)|(contract|assignment) of \d{1,2} (months|weeks)|temp[- ]to[- ]perm)\b", re.I)
_CONTRACT = re.compile(r"\bcontract(or)?\b", re.I)
_PERMANENT = re.compile(r"\bpermanent\b", re.I)


def short_term_role(job: dict | None) -> bool:
    """The ad is for contract, temp, casual or fixed-term work (not permanent, and not unclear)."""
    if not job:
        return False
    text = f"{job.get('title') or ''}\n{(job.get('full_description') or job.get('description') or '')[:8000]}"
    if _SHORT_TERM.search(text):
        return True
    # "Contract" alone is short-term unless the ad also says permanent ("contract management")
    return bool(_CONTRACT.search(text)) and not _PERMANENT.search(text)


# The kind of work an ad offers, for the Apply jobs list (filter and sort)
JOB_KINDS = ["Permanent", "Contract", "Fixed term", "Temp", "Casual", "Temp to perm", "Not stated"]
_TEMP_TO_PERM = re.compile(r"\btemp(orary)?[- ]to[- ]perm(anent)?\b", re.I)
_FIXED_TERM = re.compile(
    r"\b(fixed[- ]term|(maternity|parental)( leave)? (cover|contract)|\d{1,2}[- ]?(month|week)s? (fixed|contract|role|"
    r"assignment|position|engagement)|(contract|assignment) of \d{1,2} (months|weeks))\b", re.I)
_TEMP = re.compile(r"\b(temp|temporary|locum|seasonal|harvest)\b(?! (residen|visa|graduate|skill))", re.I)
_CASUAL = re.compile(r"(?<!smart )"
                     r"\bcasual\b(?! (dress|attire|environment|atmosphere|culture|vibe|office|chat))", re.I)
_CONTRACT_KIND = re.compile(r"\b(contract(or)?|day rate|daily rate|per day|p/d|outside ir35|inside ir35)\b", re.I)


def job_kind(job: dict | None) -> str:
    """Permanent, Contract, Fixed term, Temp, Casual, Temp to perm, or Not stated (from the title, then the ad)."""
    if not job:
        return "Not stated"
    title = job.get("title") or ""
    body = (job.get("full_description") or job.get("description") or "")[:8000]
    salary = job.get("salary") or ""
    for text in (title, f"{title}\n{salary}\n{body}"):
        if _TEMP_TO_PERM.search(text):
            return "Temp to perm"
        if _FIXED_TERM.search(text):
            return "Fixed term"
        if _CASUAL.search(text):
            return "Casual"
        if _TEMP.search(text):
            return "Temp"
        permanent = bool(_PERMANENT.search(text))
        if _CONTRACT_KIND.search(text) and not permanent:
            return "Contract"
        if permanent:
            return "Permanent"
    return "Not stated"


def shown_work_rights(line: str, job: dict | None = None, decided: bool = False) -> str:
    """The work-rights wording for the header, or "" to leave it off.

    With Automatic, a working holiday visa shows on resumes for contract, temp and casual
    roles (it's what those recruiters ask first) and not for permanent or unclear ones;
    `decided` passes it through as tailoring already chose for that job.
    """
    mode = work_rights_mode()
    if not line or mode == "never":
        return ""
    if mode == "auto" and _LIMITED.search(line):
        if re.search(r"full work", line, re.I):  # a temporary visa with full work rights reads as just that
            return "Full working rights"
        if re.search(r"working holiday|youth mobility", line, re.I) and (decided or short_term_role(job)):
            # The UK's Youth Mobility Scheme is treated like Australia's working holiday visa
            return "Youth Mobility Scheme visa" if re.search(r"youth mobility", line, re.I) else "Working holiday visa"
        return ""
    return line


def header_details(line: str, keep_working_holiday: bool = True) -> str:
    """A "Sydney, NSW | Working holiday visa" header line with the work rights shown or not.
    A working holiday visa already in a tailored resume was chosen for its job, so it stays
    (unless `keep_working_holiday` is False, for your own Word header on a permanent role)."""
    parts = []
    for part in (p.strip() for p in (line or "").split("|")):
        if part and _WORK_RIGHTS.search(part):
            part = shown_work_rights(part, decided=keep_working_holiday)
        if part:
            parts.append(part)
    return " | ".join(parts)


def work_rights_left_off(job: dict | None = None) -> bool:
    """The candidate chose to keep their work rights off this resume (so the ATS check doesn't
    ask for them)."""
    mode = work_rights_mode()
    if mode != "auto":
        return mode == "never"
    from applypilot.config import load_profile
    from applypilot.scoring.context import work_rights
    try:
        line = work_rights(load_profile())
    except Exception:  # no profile yet
        return False
    return bool(line) and not shown_work_rights(line, job=job)


def pages_target(profile: dict | None = None) -> int:
    """How many pages a resume should fill: RESUME_PAGES 1 or 2, or automatically one page for
    graduates and early career (2 years or less), two otherwise."""
    value = os.environ.get("RESUME_PAGES", "").strip()
    if value in ("1", "2"):
        return int(value)
    if profile is None:
        from applypilot.config import load_profile
        try:
            profile = load_profile()
        except Exception:  # no profile yet
            return 2
    try:
        years = float(str((profile.get("experience") or {}).get("years_of_experience_total") or "").strip())
    except ValueError:
        return 2
    return 1 if years <= 2 else 2


def length_rule(profile: dict | None = None) -> str:
    """The LENGTH instruction for the tailoring prompt."""
    if pages_target(profile) == 1:
        return ("LENGTH: ONE page (a graduate or early-career resume). About 400-450 words in all: a 2-sentence "
                "summary, 3-4 skills lines, 3-4 bullets for the most relevant role or project and 1-2 for the "
                "rest. Drop the least relevant roles' bullets, projects and skills first. No filler.")
    from applypilot import market
    mk = market.current(profile)
    return (f"LENGTH: {mk.adjective} {mk.documents} are normally 2 pages (3 at most for long careers). Keep it "
            "tight: no "
            "filler bullets.")


def plain_title(title: str) -> str:
    """The headline without the tagline, unless it's switched on."""
    return title if tagline() else (title or "").split(" · ", 1)[0].strip()


# -- CSS -----------------------------------------------------------------------

_BASE = """
* { margin: 0; padding: 0; box-sizing: border-box; }
a { color: inherit; text-decoration: none; }
.skill-row { margin: 1px 0; }
.entry { break-inside: avoid; }
.entry-head { display: flex; justify-content: space-between; gap: 12px; }
.entry-dates { white-space: nowrap; }
ul { margin: 2px 0 0 16px; }
li { margin-bottom: 2px; }
"""
_SANS = "'Calibri', 'Carlito', 'Segoe UI', Arial, sans-serif"
_SERIF = "'Georgia', 'Cambria', 'Times New Roman', 'DejaVu Serif', serif"

_LAYOUT_CSS = {
    # Left-aligned, black text, the accent only on section headings
    "modern": f"""
@page {{ size: A4; margin: 1.6cm 1.9cm; }}
body {{ font-family: {_SANS}; font-size: 10.5pt; line-height: 1.38; color: #1a1a1a; }}
.header {{ margin-bottom: 4px; }}
.name {{ font-size: 21pt; font-weight: 700; color: #111; letter-spacing: -0.2px; }}
.title {{ font-size: 11.5pt; color: #333; margin: 1px 0 2px; }}
.location, .contact {{ font-size: 10pt; color: #555; }}
.section {{ margin-top: 11px; }}
.section-title {{ font-size: 10.5pt; font-weight: 700; color: var(--accent); text-transform: uppercase;
                 letter-spacing: 0.9px; margin-bottom: 3px; }}
.skill-cat {{ font-weight: 700; }}
.entry {{ margin-bottom: 7px; }}
.entry-title {{ font-weight: 700; color: #111; }}
.entry-dates {{ color: #444; }}
.entry-subtitle {{ font-size: 10pt; color: #555; }}
""",
    # Serif, centred name, small-caps headings over a thin rule
    "classic": f"""
@page {{ size: A4; margin: 1.7cm 2cm; }}
body {{ font-family: {_SERIF}; font-size: 10.5pt; line-height: 1.38; color: #1a1a1a; }}
.header {{ text-align: center; margin-bottom: 6px; }}
.name {{ font-size: 20pt; font-weight: 400; letter-spacing: 1.5px; color: var(--accent); }}
.title {{ font-size: 11pt; font-style: italic; color: #333; margin: 2px 0; }}
.location, .contact {{ font-size: 9.8pt; color: #444; }}
.section {{ margin-top: 10px; }}
.section-title {{ font-size: 11.5pt; font-variant: small-caps; letter-spacing: 1px; color: #111;
                 border-bottom: 0.75px solid #333; padding-bottom: 1px; margin-bottom: 4px; }}
.skill-cat {{ font-weight: 700; }}
.entry {{ margin-bottom: 7px; }}
.entry-title {{ font-weight: 700; }}
.entry-dates {{ color: #333; font-style: italic; }}
.entry-subtitle {{ font-size: 10pt; color: #444; }}
""",
    # Tighter type and spacing for long careers; a short accent bar marks each heading
    "compact": f"""
@page {{ size: A4; margin: 1.2cm 1.5cm; }}
body {{ font-family: {_SANS}; font-size: 9.8pt; line-height: 1.28; color: #1a1a1a; }}
.header {{ margin-bottom: 2px; }}
.name {{ font-size: 17pt; font-weight: 700; color: #111; }}
.title {{ font-size: 10.5pt; color: #333; }}
.location, .contact {{ font-size: 9.3pt; color: #555; }}
.section {{ margin-top: 8px; }}
.section-title {{ font-size: 10.3pt; font-weight: 700; color: #111; border-left: 3px solid var(--accent);
                 padding-left: 6px; margin-bottom: 3px; }}
.skill-cat {{ font-weight: 700; }}
.entry {{ margin-bottom: 5px; }}
.entry-title {{ font-weight: 700; }}
.entry-dates {{ color: #444; }}
.entry-subtitle {{ font-size: 9.3pt; color: #555; }}
li {{ margin-bottom: 1px; }}
""",
    # The original look: centred header, coloured headings with rules
    "traditional": f"""
@page {{ size: A4; margin: 1.5cm 1.8cm; }}
body {{ font-family: {_SANS}; font-size: 10.5pt; line-height: 1.35; color: #1a1a1a; }}
.header {{ text-align: center; margin-bottom: 6px; padding-bottom: 5px; border-bottom: 1.5px solid var(--accent); }}
.name {{ font-size: 20pt; font-weight: 700; color: var(--accent); }}
.title {{ font-size: 11.5pt; color: #333; margin: 1px 0; }}
.location, .contact {{ font-size: 10pt; color: #444; }}
.section {{ margin-top: 9px; }}
.section-title {{ font-size: 11pt; font-weight: 700; color: var(--accent); text-transform: uppercase;
                 letter-spacing: 0.6px; border-bottom: 1px solid var(--accent); padding-bottom: 1px;
                 margin-bottom: 4px; }}
.skill-cat {{ font-weight: 700; color: var(--accent); }}
.entry {{ margin-bottom: 7px; }}
.entry-title {{ font-weight: 700; color: var(--accent); }}
.entry-dates {{ color: #333; }}
.entry-subtitle {{ font-size: 10pt; color: #555; }}
""",
}


def resume_css(name: str | None = None, colour: str | None = None) -> str:
    name = name or layout()
    root = f":root {{ --accent: {colour or accent()}; }}" + _BASE
    if name in OWN_STYLE:
        # Your resume PDF's style (a "word" resume's PDF copy too); Modern until there is one
        from applypilot.scoring import style_match
        style = style_match.load()
        if style:
            return root + style_match.resume_css(style)
        name = "modern"
    return root + _LAYOUT_CSS.get(name, _LAYOUT_CSS["modern"])


SAMPLE = """Jane Morgan
Senior Data Scientist · Production ML & LLM Systems
Wentworthville, NSW | Australian Citizen
jane@example.com | 0412 345 678 | linkedin.com/in/janemorgan

SUMMARY
Data scientist with 7 years building recommender and forecasting systems in Python and SQL on Google Cloud.

TECHNICAL SKILLS
Languages: Python, SQL
Machine learning: PyTorch, scikit-learn, recommender systems, A/B testing

EXPERIENCE
Senior Data Scientist at Harbourline Retail Group
Jan 2021 - Present | Python, PyTorch, BigQuery
- Technical lead for production recommender systems reaching 10 million customers
- Rebuilt the recommendation pipelines on Vertex AI, cutting running costs by 50%

Data Scientist at Harbourline Retail Group
Sep 2019 - Dec 2020 | Python, SQL
- Built demand forecasts for 300 stores used in weekly ordering

EDUCATION
Southern Cross Institute of Technology | Master of Data Science | 2019
"""


def fit_css(level: int) -> str:
    """Tighter spacing and type, used when a resume runs past its page target (level 1 or 2)."""
    size, lead, margin = ((10.0, 1.28, "1.3cm 1.6cm"), (9.4, 1.22, "1.0cm 1.3cm"))[min(level, 2) - 1]
    return (f"@page {{ margin: {margin}; }} body {{ font-size: {size}pt !important; line-height: {lead} "
            "!important; } .section { margin-top: 6px !important; } .entry { margin-bottom: 4px !important; } "
            "li { margin-bottom: 0 !important; } .name { font-size: 1.6em !important; }")


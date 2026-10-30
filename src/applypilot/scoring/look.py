"""How the resume PDF looks (Setup -> Resume look), applied when the PDF is made.

- RESUME_LAYOUT: modern (default), classic, compact or traditional. All one column of
  real text, so ATS read them the same way.
- RESUME_ACCENT: the one colour used for section headings (and the name in classic).
- RESUME_TAGLINE: "on" adds the AI's few words after your job title
  ("Senior Data Scientist · Production ML"). Off by default: it reads as AI-tailored.
- RESUME_TECH_LINE: "on" shows the tools line under each job. Off by default: the
  bullets and skills already name them.
- SHOW_WORK_RIGHTS: auto (default) shows citizenship, residency and full work rights,
  and leaves temporary visas with limits (working holiday, student) off; always; never.

Applied when the PDF is rendered, so changing one and running "PDF" again restyles
every resume without asking the AI.
"""

from __future__ import annotations

import os
import re

LAYOUTS = {"modern": "Modern", "classic": "Classic", "compact": "Compact", "traditional": "Traditional"}
ACCENTS = {"navy": "#1f3a5f", "teal": "#0f5e63", "green": "#2f5d3a", "burgundy": "#6b1f2a",
           "plum": "#4b2e5a", "charcoal": "#2b2b2b"}
WORK_RIGHTS_MODES = ("auto", "always", "never")

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


def shown_work_rights(line: str) -> str:
    """The work-rights wording for the header, or "" to leave it off."""
    mode = work_rights_mode()
    if not line or mode == "never":
        return ""
    if mode == "auto" and _LIMITED.search(line):
        # A temporary visa with full work rights reads as just that; a limited one is left off
        return "Full working rights" if re.search(r"full work", line, re.I) else ""
    return line


def header_details(line: str) -> str:
    """A "Sydney, NSW | Working holiday visa" header line with the work rights shown or not."""
    parts = []
    for part in (p.strip() for p in (line or "").split("|")):
        if part and _WORK_RIGHTS.search(part):
            part = shown_work_rights(part)
        if part:
            parts.append(part)
    return " | ".join(parts)


def work_rights_left_off() -> bool:
    """The candidate chose to keep their work rights off (so the ATS check doesn't ask for them)."""
    mode = work_rights_mode()
    if mode != "auto":
        return mode == "never"
    from applypilot.config import load_profile
    from applypilot.scoring.context import work_rights
    try:
        line = work_rights(load_profile())
    except Exception:  # no profile yet
        return False
    return bool(line) and not shown_work_rights(line)


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
    return (f":root {{ --accent: {colour or accent()}; }}" + _BASE
            + _LAYOUT_CSS.get(name or layout(), _LAYOUT_CSS["modern"]))


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


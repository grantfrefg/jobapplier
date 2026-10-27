"""Text-to-PDF conversion for tailored resumes and cover letters.

Parses the structured text resume format, renders via an HTML/CSS template,
and exports to PDF using headless Chromium via Playwright.
"""

import html as html_lib
import logging
import re
from pathlib import Path

from applypilot.config import TAILORED_DIR
from applypilot.scoring.context import location_line

log = logging.getLogger(__name__)


# ── Resume Parser ────────────────────────────────────────────────────────

def parse_resume(text: str) -> dict:
    """Parse a structured text resume into sections.

    Expects a format with header lines (name, title, location, contact)
    followed by ALL-CAPS section headers (SUMMARY, TECHNICAL SKILLS, etc.).

    Args:
        text: Full resume text.

    Returns:
        {"name": str, "title": str, "location": str, "contact": str, "sections": dict}
    """
    lines = [line.rstrip() for line in text.strip().split("\n")]

    # Header: first few lines before SUMMARY
    header_lines: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip().upper() == "SUMMARY":
            body_start = i
            break
        if line.strip():
            header_lines.append(line.strip())

    name = header_lines[0] if len(header_lines) > 0 else ""
    title = header_lines[1] if len(header_lines) > 1 else ""
    # The header may have 3 or 4 lines depending on whether location is included
    location = ""
    contact = ""
    if len(header_lines) > 3:
        location = header_lines[2]
        contact = header_lines[3]
    elif len(header_lines) > 2:
        # Could be location or contact -- check for email/phone indicators
        if "@" in header_lines[2] or "|" in header_lines[2]:
            contact = header_lines[2]
        else:
            location = header_lines[2]

    # Split body into sections by ALL-CAPS headers
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in lines[body_start:]:
        stripped = line.strip()
        # Detect section headers (all caps, no leading dash/bullet, longer than 3 chars)
        if (
            stripped
            and stripped == stripped.upper()
            and not stripped.startswith("-")
            and len(stripped) > 3
            and not stripped.startswith("\u2022")
        ):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return {
        "name": name,
        "title": title,
        "location": location,
        "contact": contact,
        "sections": sections,
    }


def parse_skills(text: str) -> list[tuple[str, str]]:
    """Parse skills section into (category, value) pairs.

    Args:
        text: The TECHNICAL SKILLS section text.

    Returns:
        List of (category_name, skills_string) tuples.
    """
    skills: list[tuple[str, str]] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            cat, val = line.split(":", 1)
            skills.append((cat.strip(), val.strip()))
    return skills


def parse_entries(text: str) -> list[dict]:
    """Parse experience/project entries from section text.

    Args:
        text: The EXPERIENCE or PROJECTS section text.

    Returns:
        List of {"title": str, "subtitle": str, "bullets": list[str]} dicts.
    """
    entries: list[dict] = []
    lines = text.strip().split("\n")
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("\u2022 "):
            if current:
                current["bullets"].append(stripped[2:].strip())
        elif current is None or (
            not stripped.startswith("-")
            and not stripped.startswith("\u2022")
            and len(current.get("bullets", [])) > 0
        ):
            # New entry
            if current:
                entries.append(current)
            current = {"title": stripped, "subtitle": "", "bullets": []}
        elif current and not current["subtitle"]:
            current["subtitle"] = stripped
        else:
            if current:
                current["bullets"].append(stripped)

    if current:
        entries.append(current)

    return entries


# ── HTML Template ────────────────────────────────────────────────────────

# A date range in an entry's subtitle ("Mar 2021 - Present", "2019 - 2021", "2022")
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_YEAR = r"(?<!\d)(?:19|20)\d{2}(?!\d)"
_DATE_RANGE = re.compile(
    rf"((?:{_MONTH}\s+)?{_YEAR}\s*[-–—]+\s*(?:(?:{_MONTH}\s+)?{_YEAR}|Present|Current|Now)|(?:{_MONTH}\s+)?{_YEAR})",
    re.IGNORECASE,
)


def _esc(text: str) -> str:
    return html_lib.escape(text or "", quote=False)


def split_entry(title: str, subtitle: str, job: bool = True) -> tuple[str, str, str]:
    """(heading, dates, detail) for an experience/project entry.

    "Software Engineer at Acme Health" becomes "Software Engineer, Acme
    Health" so title and employer read as separate fields, and the dates are
    lifted out of the subtitle ("Python, AWS | Mar 2021 - Present") to sit on
    the heading line, the order ATS parsers expect.
    """
    heading = title.strip()
    if job:  # "Title at Company" / "Title | Company" -> "Title, Company"
        heading = heading.replace(" | ", ", ", 1) if " | " in heading else \
            re.sub(r"\s+at\s+(?=[A-Z0-9])", ", ", heading, count=1)
    dates = ""
    match = _DATE_RANGE.search(subtitle or "")
    if match:
        dates = re.sub(r"\s*[-–—]+\s*", " – ", match.group(1).strip())
        detail = (subtitle[:match.start()] + subtitle[match.end():]).strip(" |,;-")
        detail = re.sub(r"\s*\|\s*\|\s*", " | ", detail)
    else:
        detail = subtitle or ""
    return heading, dates, detail.strip()


def _linkify(part: str) -> str:
    text = _esc(part)
    if "@" in part and " " not in part:
        return f'<a href="mailto:{text}">{text}</a>'
    if re.match(r"https?://", part):
        return f'<a href="{text}">{text.split("://", 1)[1].rstrip("/")}</a>'
    return text


def _entries_html(text: str, job: bool = True) -> str:
    items = ""
    for e in parse_entries(text):
        heading, dates, detail = split_entry(e["title"], e["subtitle"], job)
        bullets = "".join(f"<li>{_esc(b)}</li>" for b in e["bullets"])
        detail_html = f'<div class="entry-subtitle">{_esc(detail)}</div>' if detail else ""
        items += (f'<div class="entry"><div class="entry-head"><span class="entry-title">{_esc(heading)}</span>'
                  f'<span class="entry-dates">{_esc(dates)}</span></div>{detail_html}'
                  f'{f"<ul>{bullets}</ul>" if bullets else ""}</div>')
    return items


_CSS = """
@page { size: A4; margin: 1.5cm 1.8cm; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Calibri', 'Carlito', 'Segoe UI', Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.35; color: #1a1a1a; }
a { color: inherit; text-decoration: none; }
.header { text-align: center; margin-bottom: 6px; padding-bottom: 5px; border-bottom: 1.5px solid #2a7ab5; }
.name { font-size: 20pt; font-weight: 700; color: #1a3a5c; }
.title { font-size: 11.5pt; color: #3a6b8c; margin: 1px 0; }
.location, .contact { font-size: 10pt; color: #444; }
.section { margin-top: 9px; }
.section-title { font-size: 11pt; font-weight: 700; color: #1a3a5c; text-transform: uppercase;
                 letter-spacing: 0.6px; border-bottom: 1px solid #2a7ab5; padding-bottom: 1px; margin-bottom: 4px; }
.skill-row { margin: 1px 0; }
.skill-cat { font-weight: 700; color: #1a3a5c; }
.entry { margin-bottom: 7px; break-inside: avoid; }
.entry-head { display: flex; justify-content: space-between; gap: 12px; }
.entry-title { font-weight: 700; color: #1a3a5c; }
.entry-dates { white-space: nowrap; color: #333; }
.entry-subtitle { font-size: 10pt; color: #4a6a85; font-style: italic; }
ul { margin: 2px 0 0 16px; }
li { margin-bottom: 2px; }
"""


def build_html(resume: dict) -> str:
    """Build an ATS-friendly A4 resume page from parsed data.

    One column, real text, standard section headings, no tables or images.
    Empty sections are left out.
    """
    sections = resume["sections"]
    parts: list[str] = []

    def section(title: str, body: str) -> None:
        if body.strip():
            parts.append(f'<div class="section"><div class="section-title">{title}</div>{body}</div>')

    section("Summary", f'<div class="summary">{_esc(sections.get("SUMMARY", "").strip())}</div>'
            if sections.get("SUMMARY", "").strip() else "")
    skills = parse_skills(sections.get("TECHNICAL SKILLS", ""))
    section("Skills", "".join(f'<div class="skill-row"><span class="skill-cat">{_esc(c)}:</span> {_esc(v)}</div>'
                              for c, v in skills))
    section("Experience", _entries_html(sections.get("EXPERIENCE", "")))
    section("Projects", _entries_html(sections.get("PROJECTS", ""), job=False))
    edu = sections.get("EDUCATION", "").strip()
    section("Education", f'<div class="edu">{_esc(edu)}</div>' if edu else "")

    contact = resume["contact"]
    contact_html = " &nbsp;|&nbsp; ".join(_linkify(p.strip()) for p in contact.split("|")) if contact else ""
    location_html = f'<div class="location">{_esc(resume["location"])}</div>' if resume["location"] else ""
    name = _esc(resume["name"])
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<title>{name} - Resume</title>
<meta name="author" content="{name}">
<style>{_CSS}</style>
</head>
<body>
<div class="header">
    <div class="name">{name}</div>
    <div class="title">{_esc(resume['title'])}</div>
    {location_html}
    <div class="contact">{contact_html}</div>
</div>
{"".join(parts)}
</body>
</html>"""


_LETTER_CSS = """
@page { size: A4; margin: 2.2cm 2.4cm; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Calibri', 'Carlito', 'Segoe UI', Arial, sans-serif; font-size: 11pt;
       line-height: 1.45; color: #1a1a1a; }
a { color: inherit; text-decoration: none; }
.sender { margin-bottom: 18px; }
.sender .name { font-size: 16pt; font-weight: 700; color: #1a3a5c; }
.sender .contact { font-size: 10pt; color: #444; }
.date { margin-bottom: 14px; }
p { margin-bottom: 10px; }
"""


def is_cover_letter(text: str) -> bool:
    """Cover letters start with the salutation; resumes start with the name."""
    return text.lstrip().lower().startswith("dear ")


def build_letter_html(text: str, profile: dict | None = None, today=None) -> str:
    """A plain A4 business letter: your details, the date, then every paragraph."""
    from datetime import date
    profile = profile or {}
    personal = profile.get("personal", {})
    name = personal.get("full_name", "")
    contact = [x for x in (location_line(profile), personal.get("phone"), personal.get("email"),
                           personal.get("linkedin_url")) if x]
    sender = ""
    if name:
        sender = (f'<div class="sender"><div class="name">{_esc(name)}</div>'
                  f'<div class="contact">{" &nbsp;|&nbsp; ".join(_linkify(c) for c in contact)}</div></div>')
    day = today or date.today()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    body = "".join(f"<p>{'<br>'.join(_esc(line) for line in para.splitlines())}</p>" for para in paragraphs)
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<title>{_esc(name) or "Cover letter"} - Cover Letter</title>
<meta name="author" content="{_esc(name)}">
<style>{_LETTER_CSS}</style>
</head>
<body>
{sender}
<div class="date">{day.day} {day:%B %Y}</div>
{body}
</body>
</html>"""


# ── PDF Renderer ─────────────────────────────────────────────────────────

def _launch_headless(p):
    """A headless Chromium for printing, from whatever browser is installed.

    Playwright's default headless build ("headless shell") isn't downloaded by
    the app's "Install browser" button (it uses --no-shell), so fall back to
    the full Chromium in headless mode, then to an installed Chrome / Edge.
    """
    from applypilot import config

    errors = []
    attempts = [{}, {"channel": "chromium"}] + [{"executable_path": exe} for exe in config.find_system_browsers()]
    for kwargs in attempts:
        try:
            return p.chromium.launch(headless=True, **kwargs)
        except Exception as e:
            errors.append(str(e).strip().splitlines()[0][:120])
    raise RuntimeError("No browser available to make PDFs: " + " | ".join(errors))


def render_pdf(html: str, output_path: str) -> None:
    """Render HTML to an A4 PDF using Playwright's headless Chromium (page size and margins from the CSS)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch_headless(p)
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(path=output_path, prefer_css_page_size=True, print_background=True)
        browser.close()


# ── Public API ───────────────────────────────────────────────────────────

def convert_to_pdf(
    text_path: Path, output_path: Path | None = None, html_only: bool = False
) -> Path:
    """Convert a text resume/cover letter to PDF.

    Args:
        text_path: Path to the .txt file to convert.
        output_path: Optional override for the output path. Defaults to same
            name with .pdf extension.
        html_only: If True, output HTML instead of PDF.

    Returns:
        Path to the generated PDF (or HTML) file.
    """
    text_path = Path(text_path)
    text = text_path.read_text(encoding="utf-8")
    if is_cover_letter(text):
        try:
            from applypilot.config import load_profile
            profile = load_profile()
        except Exception:  # no profile yet: the letter still renders, without the sender block
            profile = {}
        html = build_letter_html(text, profile)
    else:
        html = build_html(parse_resume(text))

    if html_only:
        out = output_path or text_path.with_suffix(".html")
        out = Path(out)
        out.write_text(html, encoding="utf-8")
        log.info("HTML generated: %s", out)
        return out

    out = output_path or text_path.with_suffix(".pdf")
    out = Path(out)
    render_pdf(html, str(out))
    log.info("PDF generated: %s", out)
    return out


# Bump when the PDF layout changes: existing PDFs are then rendered again once
# from their .txt (v2: A4, letter layout for cover letters, title/date lines).
LAYOUT_VERSION = "2"


def batch_convert(limit: int = 50) -> int:
    """Make PDFs for tailored resumes and cover letters that don't have one yet.

    Skips _JOB and _REJECTED files. When the layout has changed since the
    PDFs were made (LAYOUT_VERSION), every existing PDF is rendered again
    from its text, so old documents get the new layout too.

    Returns:
        Number of PDFs generated.
    """
    from applypilot.config import COVER_LETTER_DIR

    if not TAILORED_DIR.exists():
        log.warning("Tailored directory does not exist: %s", TAILORED_DIR)
        return 0

    marker = TAILORED_DIR / ".pdf-layout"
    try:
        rerender = marker.read_text(encoding="utf-8").strip() != LAYOUT_VERSION
    except OSError:
        rerender = True
    candidates = [f for d in (TAILORED_DIR, COVER_LETTER_DIR) if d.exists() for f in sorted(d.glob("*.txt"))
                  if not f.name.endswith(("_JOB.txt", "_REJECTED.txt"))]
    if rerender:
        to_convert = [f for f in candidates if f.with_suffix(".pdf").exists()]
        to_convert += [f for f in candidates if not f.with_suffix(".pdf").exists()][:limit]
        if to_convert:
            log.info("PDF layout updated: rendering %d documents again...", len(to_convert))
    else:
        to_convert = [f for f in candidates if not f.with_suffix(".pdf").exists()][:limit]

    if not to_convert:
        log.info("All text files already have PDFs.")
        marker.write_text(LAYOUT_VERSION, encoding="utf-8")
        return 0

    log.info("Converting %d files to PDF...", len(to_convert))
    converted = failed = 0
    for f in to_convert:
        try:
            convert_to_pdf(f)
            converted += 1
        except Exception as e:
            failed += 1
            log.error("Failed to convert %s: %s", f.name, e)
    if not failed:
        marker.write_text(LAYOUT_VERSION, encoding="utf-8")

    log.info("Done: %d/%d PDFs generated", converted, len(to_convert))
    return converted

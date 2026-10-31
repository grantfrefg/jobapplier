"""Your own resume's look, read from the PDF you uploaded (Resume look -> "Match my resume").

Reads the fonts, sizes, colours, alignment, section-heading style, rules under the
headings, bullet character and margins of your resume PDF, and turns them into the
CSS for tailored resumes. The result stays one column of real text, so a two-column
or graphic design comes out as its single-column equivalent.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import Counter
from pathlib import Path

from applypilot import config

log = logging.getLogger(__name__)

PT_PER_CM = 72 / 2.54
_HEADINGS = re.compile(
    r"^(professional |career |work |employment |technical |core |key |relevant )?"
    r"(summary|profile|objective|about me|experience|employment|work history|history|skills|"
    r"technical skills|competencies|expertise|education|qualifications|projects|certifications|"
    r"certificates|awards|achievements|publications|volunteering|interests|referees|references|"
    r"languages|training|courses)( and [a-z ]+)?:?$", re.I)
_BULLETS = "•●▪■◦○◆◇►▸➢➤✓✔–-*·"
_SERIF = re.compile(r"times|garamond|georgia|cambria|book|serif|roman|minion|palatino|baskerville|caslon|"
                    r"didot|bodoni|cmr|lmroman|charter|merriweather|playfair|lora|constantia", re.I)
_SANS_SUFFIX = re.compile(r"(PS)?MT$|PS$|-?(Bold|Italic|Oblique|Regular|Medium|Light|Semibold|SemiBold|"
                          r"Black|Heavy|Book|Roman|Condensed|Narrow|BoldItalic|BoldMT|It)+$")


def style_path() -> Path:
    return config.APP_DIR / "resume_style.json"


def font_family(pdf_name: str) -> tuple[str, bool]:
    """("Garamond", serif?) from a PDF font name such as "ABCDEF+Garamond-Bold"."""
    name = (pdf_name or "").split("+", 1)[-1]
    name = name.split(",", 1)[0]
    base = _SANS_SUFFIX.sub("", name.split("-", 1)[0]) or name
    # "TimesNewRoman" -> "Times New Roman", "OpenSans" -> "Open Sans"
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip()
    aliases = {"Arial MT": "Arial", "Helvetica Neue": "Helvetica Neue", "CMR": "Latin Modern Roman",
               "LMRoman": "Latin Modern Roman", "Symbol MT": "Symbol"}
    spaced = aliases.get(spaced, spaced)
    if re.fullmatch(r"CMR\d*|LMRoman\d*", base):
        spaced = "Latin Modern Roman"
    return spaced, bool(_SERIF.search(name))


def _is_bold(pdf_name: str) -> bool:
    return bool(re.search(r"bold|black|heavy|semibold|demi", pdf_name or "", re.I))


def _colour(value) -> str:
    """#rrggbb from a pdfplumber colour (gray, RGB or CMYK tuple)."""
    try:
        v = [float(x) for x in (value if isinstance(value, (list, tuple)) else [value])]
    except (TypeError, ValueError):
        return "#000000"
    if len(v) == 1:
        r = g = b = v[0]
    elif len(v) == 3:
        r, g, b = v
    elif len(v) == 4:
        c, m, y, k = v
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    else:
        return "#000000"
    return "#" + "".join(f"{max(0, min(255, round(x * 255))):02x}" for x in (r, g, b))


def _lines(page) -> list[dict]:
    """Text lines on a page, each with its chars' main font, size, colour and extent."""
    rows: dict[int, list] = {}
    for ch in page.chars:
        if not ch["text"].strip():
            continue
        rows.setdefault(round(ch["top"] / 2), []).append(ch)
    out = []
    for key in sorted(rows):
        chars = sorted(rows[key], key=lambda c: c["x0"])
        text = ""
        last = None
        for c in chars:
            if last is not None and c["x0"] - last > c["size"] * 0.25:
                text += " "
            text += c["text"]
            last = c["x1"]
        font, _ = Counter(c["fontname"] for c in chars).most_common(1)[0]
        size = statistics.median(c["size"] for c in chars)
        colour = Counter(_colour(c.get("non_stroking_color")) for c in chars).most_common(1)[0][0]
        out.append({"text": text.strip(), "font": font, "size": round(size, 1), "colour": colour,
                    "x0": chars[0]["x0"], "x1": chars[-1]["x1"], "top": min(c["top"] for c in chars),
                    "bottom": max(c["bottom"] for c in chars), "chars": len(chars)})
    return out


def _centred(line: dict, width: float) -> bool:
    return abs((line["x0"] + line["x1"]) / 2 - width / 2) < width * 0.04 and line["x0"] > width * 0.12


def extract(pdf_path: Path) -> dict:
    """The style of a resume PDF as a dict (see resume_css); empty if it can't be read."""
    import pdfplumber
    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            return {}
        page = pdf.pages[0]
        width, height = float(page.width), float(page.height)
        lines = _lines(page)
        rules = [r for r in list(page.lines) + list(page.rects)
                 if abs(r["bottom"] - r["top"]) < 3 and r["x1"] - r["x0"] > width * 0.25]
        rule_colours = [_colour(r.get("stroking_color") or r.get("non_stroking_color")) for r in rules]
        extra = []
        for p in pdf.pages[1:2]:
            extra = _lines(p)
    if not lines:
        return {}

    body_chars = Counter()
    for line in lines + extra:
        body_chars[(line["font"], line["size"])] += line["chars"]
    (body_font, body_size), _ = body_chars.most_common(1)[0]
    body_colour = Counter(ln["colour"] for ln in lines if ln["font"] == body_font).most_common(1)[0][0]

    name = max(lines[:4], key=lambda ln: ln["size"])
    headings = [ln for ln in lines if _HEADINGS.match(ln["text"].strip()) and ln is not name]
    heading = headings[0] if headings else None
    header = [ln for ln in lines if ln["top"] < (heading["top"] if heading else name["bottom"] + body_size * 5)][:5]
    rule_under = rule_colour = None
    if heading:
        for r, colour in zip(rules, rule_colours):
            if 0 <= r["top"] - heading["bottom"] < body_size * 1.2:
                rule_under, rule_colour = round(max(0.5, r["bottom"] - r["top"]) or 0.75, 2), colour
                break
    bullet = ""
    for ln in lines + extra:
        if ln["text"][:1] in _BULLETS and len(ln["text"]) > 3 and ln["text"][1:2] in (" ", "\t", ""):
            bullet = ln["text"][0]
            break

    body_lines = sorted((ln for ln in lines if ln["font"] == body_font and abs(ln["size"] - body_size) < 0.3),
                        key=lambda ln: ln["top"])
    gaps = [b["top"] - a["top"] for a, b in zip(body_lines, body_lines[1:])
            if 0 < b["top"] - a["top"] < body_size * 2]
    line_height = round(statistics.median(gaps) / body_size, 2) if gaps else 1.3

    family, serif = font_family(body_font)
    name_family, name_serif = font_family(name["font"])
    left = min(ln["x0"] for ln in lines)
    right = width - max(ln["x1"] for ln in lines)
    top = min(ln["top"] for ln in lines)
    return {
        "font": family, "serif": serif, "size": body_size, "colour": body_colour,
        "line_height": min(max(line_height, 1.1), 1.7),
        "name": {"font": name_family, "serif": name_serif, "size": name["size"], "colour": name["colour"],
                 "bold": _is_bold(name["font"]), "centred": _centred(name, width)},
        "header_centred": sum(_centred(ln, width) for ln in header) > len(header) / 2,
        "heading": None if not heading else {
            "font": font_family(heading["font"])[0], "serif": font_family(heading["font"])[1],
            "size": heading["size"], "colour": heading["colour"], "bold": _is_bold(heading["font"]),
            "upper": heading["text"].isupper(), "centred": _centred(heading, width),
            "rule": rule_under, "rule_colour": rule_colour},
        "bullet": bullet,
        "margins_cm": [round(max(0.8, min(top, 3 * PT_PER_CM)) / PT_PER_CM, 2),
                       round(max(0.8, min(right, 3 * PT_PER_CM)) / PT_PER_CM, 2),
                       round(max(0.8, min(top, 3 * PT_PER_CM)) / PT_PER_CM, 2),
                       round(max(0.8, min(left, 3 * PT_PER_CM)) / PT_PER_CM, 2)],
        "page": "A4" if abs(height - 842) < 20 else "Letter" if abs(height - 792) < 20 else "A4",
    }


def save_from(pdf_path: Path) -> dict:
    """Read the style of the uploaded resume PDF and keep it for rendering. {} if unreadable."""
    try:
        style = extract(pdf_path)
    except Exception as e:  # a scanned or unusual PDF: the other layouts still work
        log.warning("Couldn't read the style of %s: %s", pdf_path, e)
        style = {}
    if style:
        style_path().write_text(json.dumps(style, indent=2), encoding="utf-8")
    else:
        style_path().unlink(missing_ok=True)
    return style


def load() -> dict:
    """Your resume's style, read again if the PDF is newer than the saved one."""
    pdf, saved = config.RESUME_PDF_PATH, style_path()
    if pdf.exists() and (not saved.exists() or saved.stat().st_mtime < pdf.stat().st_mtime):
        return save_from(pdf)
    try:
        return json.loads(saved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _family(name: str, serif: bool) -> str:
    """The font by name (spaced and not: "Open Sans" / "OpenSans"), then a close fallback."""
    fallback = ("'Georgia', 'Cambria', 'Times New Roman', 'DejaVu Serif', serif" if serif
                else "'Calibri', 'Carlito', 'Segoe UI', Arial, sans-serif")
    clean = re.sub(r"[^\w \-]", "", name or "").strip()
    names = list(dict.fromkeys(n for n in (clean, clean.replace(" ", "")) if n))
    return ", ".join([f"'{n}'" for n in names] + [fallback])


def resume_css(style: dict) -> str:
    """CSS for a tailored resume in your own resume's style (the layout CSS look.py uses)."""
    s = style
    name, heading = s.get("name") or {}, s.get("heading") or {}
    top, right, bottom, left = s.get("margins_cm") or [1.5, 1.8, 1.5, 1.8]
    centre = "center" if s.get("header_centred") else "left"
    rule = (f"border-bottom: {heading['rule']}pt solid {heading.get('rule_colour') or heading.get('colour')};"
            if heading.get("rule") else "")
    bullet = s.get("bullet") or ""
    marker = ""
    if bullet and bullet not in "•●":  # the browser's own disc already looks like these
        marker = ("ul { list-style: none; margin-left: 14px; } li { position: relative; } "
                  "li::before { content: '" + bullet + "'; position: absolute; left: -12px; }")
    return f"""
@page {{ size: {s.get('page', 'A4')}; margin: {top}cm {right}cm {bottom}cm {left}cm; }}
body {{ font-family: {_family(s.get('font', ''), s.get('serif', False))}; font-size: {s.get('size', 10.5)}pt;
       line-height: {s.get('line_height', 1.35)}; color: {s.get('colour', '#1a1a1a')}; }}
.header {{ text-align: {centre}; margin-bottom: 6px; }}
.name {{ font-family: {_family(name.get('font', ''), name.get('serif', False))}; font-size: {name.get('size', 20)}pt;
        font-weight: {700 if name.get('bold', True) else 400}; color: {name.get('colour', '#111')}; }}
.title {{ margin: 1px 0; }}
.location, .contact {{ font-size: {round(s.get('size', 10.5) * 0.95, 1)}pt; }}
.section {{ margin-top: {round(s.get('size', 10.5) * 0.9, 1)}pt; }}
.section-title {{ font-family: {_family(heading.get('font', s.get('font', '')), heading.get('serif', s.get('serif')))};
                 font-size: {heading.get('size', s.get('size', 10.5) + 1)}pt;
                 font-weight: {700 if heading.get('bold', True) else 400}; color: {heading.get('colour', '#111')};
                 text-transform: {'uppercase' if heading.get('upper') else 'none'};
                 text-align: {'center' if heading.get('centred') else 'left'}; {rule}
                 padding-bottom: 1px; margin-bottom: 4px; }}
.skill-cat {{ font-weight: 700; }}
.entry {{ margin-bottom: 6px; }}
.entry-title {{ font-weight: 700; }}
.entry-subtitle {{ opacity: 0.85; }}
{marker}
"""

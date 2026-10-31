"""Your own Word resume, filled with the tailored content (Resume look -> "My Word file").

A copy of the .docx you uploaded keeps every style: fonts, colours, spacing, tables, header.
Only the content that tailoring changes is replaced:
- the summary/profile paragraph(s),
- the skills lines (one per category, in the first skills line's format),
- each job's and project's bullets (matched to your entries by title and employer, in the
  first bullet's format). Job titles, employers, dates, education and your header stay
  exactly as you wrote them.
An entry tailoring left out keeps your original bullets. A resume with no recognisable
section headings can't be filled; the PDF is used instead.
"""

from __future__ import annotations

import copy
import logging
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

from applypilot import config

log = logging.getLogger(__name__)

_SECTION_OF = [
    ("SUMMARY", re.compile(r"summary|profile|objective|about me", re.I)),
    ("TECHNICAL SKILLS", re.compile(r"skills|competencies|expertise", re.I)),
    ("EXPERIENCE", re.compile(r"experience|employment|work history|career history", re.I)),
    ("PROJECTS", re.compile(r"projects", re.I)),
]
_GLYPH = re.compile(r"^\s*([•●▪■◦○◆◇►▸➢➤✓✔–\-*·])\s+")


class FillError(ValueError):
    """The Word file couldn't be filled (no sections found, or not a Word file)."""


def template_path() -> Path:
    return config.APP_DIR / "resume.docx"


def check_docx(data: bytes) -> None:
    """Refuse anything that isn't a Word (.docx) document."""
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                raise FillError("That isn't a Word document (.docx)")
    except zipfile.BadZipFile as e:
        raise FillError("That isn't a Word document (.docx). Save it from Word as .docx first.") from e


# -- reading the document ----------------------------------------------------------

def _qn(tag: str) -> str:
    from docx.oxml.ns import qn
    return qn(tag)


def _text(p) -> str:
    return "".join(t.text or "" for t in p.iter(_qn("w:t"))).strip()


def _is_bullet(p) -> bool:
    ppr = p.find(_qn("w:pPr"))
    if ppr is not None:
        if ppr.find(_qn("w:numPr")) is not None:
            return True
        style = ppr.find(_qn("w:pStyle"))
        if style is not None and "list" in (style.get(_qn("w:val")) or "").lower():
            return True
    return bool(_GLYPH.match(_text(p)))


def _heading_section(p) -> str | None:
    """The section a heading paragraph starts ("SUMMARY", ...), "" for another heading, None if not one."""
    from applypilot.scoring.style_match import _HEADINGS
    text = _text(p)
    if not text or len(text) > 40 or not _HEADINGS.match(text):
        return None
    for key, pattern in _SECTION_OF:
        if pattern.search(text):
            return key
    return ""


def _blocks(paragraphs: list) -> tuple[list, dict[str, list]]:
    """(header paragraphs, {section: its paragraphs}) in document order."""
    header, blocks, current = [], {}, None
    for p in paragraphs:
        section = _heading_section(p)
        if section is not None:
            current = section
            if section and section not in blocks:
                blocks[section] = []
            elif section:
                current = None  # a second heading of the same kind: leave it alone
            continue
        if current is None and not blocks:
            header.append(p)
        elif current:
            blocks[current].append(p)
    return header, blocks


# -- writing -----------------------------------------------------------------------

def _first_rpr(p):
    for r in p.iter(_qn("w:r")):
        if r.find(_qn("w:t")) is not None:
            rpr = r.find(_qn("w:rPr"))
            return copy.deepcopy(rpr) if rpr is not None else None
    return None


def _run(text: str, rpr, bold: bool | None = None):
    from docx.oxml import OxmlElement
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    if bold is not None:
        props = r.find(_qn("w:rPr"))
        if props is None:
            props = OxmlElement("w:rPr")
            r.insert(0, props)
        for old in props.findall(_qn("w:b")):
            props.remove(old)
        if bold:
            props.insert(0, OxmlElement("w:b"))
    t = OxmlElement("w:t")
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    r.append(t)
    return r


def _set_text(p, parts: list[tuple[str, object, bool | None]]) -> None:
    """Replace a paragraph's runs, keeping its paragraph properties."""
    for child in list(p):
        if child.tag != _qn("w:pPr"):
            p.remove(child)
    for text, rpr, bold in parts:
        p.append(_run(text, rpr, bold))


def _clone_with(template, text: str):
    """A copy of `template` (same paragraph and character format) holding `text`."""
    p = copy.deepcopy(template)
    glyph = _GLYPH.match(_text(template))
    _set_text(p, [((glyph.group(0) if glyph else "") + text, _first_rpr(template), None)])
    return p


def _replace(old: list, new: list) -> None:
    """Put `new` paragraphs where the first of `old` is, and remove `old`."""
    if not old:
        return
    anchor = old[0]
    for p in new:
        anchor.addprevious(p)
    for p in old:
        p.getparent().remove(p)


def _fill_summary(block: list, summary: str) -> bool:
    texts = [p for p in block if _text(p)]
    if not texts or not summary.strip():
        return False
    first = texts[0]
    _set_text(first, [(summary.strip(), _first_rpr(first), None)])
    for p in texts[1:]:
        if not _is_bullet(p):
            p.getparent().remove(p)
    return True


def _fill_skills(block: list, skills: list[tuple[str, str]]) -> bool:
    texts = [p for p in block if _text(p)]
    if not texts or not skills:
        return False
    template = texts[0]
    runs = [r for r in template.iter(_qn("w:r")) if r.find(_qn("w:t")) is not None]
    first_bold = bool(runs) and runs[0].find(_qn("w:rPr")) is not None and \
        runs[0].find(_qn("w:rPr")).find(_qn("w:b")) is not None
    value_rpr = runs[1].find(_qn("w:rPr")) if len(runs) > 1 else _first_rpr(template)
    new = []
    for category, values in skills:
        p = copy.deepcopy(template)
        glyph = _GLYPH.match(_text(template))
        lead = glyph.group(0) if glyph else ""
        if category:
            _set_text(p, [(f"{lead}{category}: ", _first_rpr(template), True if first_bold else None),
                          (values, value_rpr, False if first_bold else None)])
        else:
            _set_text(p, [(lead + values, _first_rpr(template), None)])
        new.append(p)
    _replace(texts, new)
    return True


def _entries(block: list) -> list[dict]:
    """Your entries in a section: {"head": [paragraphs], "bullets": [paragraphs]}."""
    entries: list[dict] = []
    for p in block:
        if not _text(p):
            continue
        if _is_bullet(p):
            if entries:
                entries[-1]["bullets"].append(p)
        elif not entries or entries[-1]["bullets"]:
            entries.append({"head": [p], "bullets": []})
        else:
            entries[-1]["head"].append(p)
    return entries


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _fill_entries(block: list, tailored: list[dict]) -> int:
    """Swap in the tailored bullets of each of your entries. Returns how many were filled."""
    used, filled = set(), 0
    for entry in _entries(block):
        if not entry["bullets"]:
            continue
        head = _words(" ".join(_text(p) for p in entry["head"]))
        best, score = None, 0.0
        for i, t in enumerate(tailored):
            if i in used or not t.get("bullets"):
                continue
            ratio = SequenceMatcher(None, head, _words(t["title"] + " " + t.get("subtitle", ""))).ratio()
            words = set(_words(t["title"]))
            overlap = len(words & set(head)) / max(len(words), 1)
            value = max(ratio, overlap)
            if value > score:
                best, score = i, value
        if best is None or score < 0.6:
            continue  # tailoring left this one out: keep your bullets
        used.add(best)
        template = entry["bullets"][0]
        _replace(entry["bullets"], [_clone_with(template, b) for b in tailored[best]["bullets"]])
        filled += 1
    return filled


def _hide_work_rights(header: list) -> None:
    from applypilot.scoring import look
    for p in header:
        text = _text(p)
        if not look._WORK_RIGHTS.search(text):
            continue
        shown = look.header_details(text)
        if shown != " | ".join(x.strip() for x in text.split("|") if x.strip()):
            _set_text(p, [(shown, _first_rpr(p), None)])


def fill(resume_text: str, out_path: Path, template: Path | None = None) -> dict:
    """Write the tailored resume into a copy of your Word resume. Returns what was filled."""
    from docx import Document

    from applypilot.scoring.pdf import parse_entries, parse_resume, parse_skills
    template = template or template_path()
    if not template.exists():
        raise FillError("No Word resume uploaded (Setup -> Your resume)")
    doc = Document(str(template))
    paragraphs = list(doc.element.body.iter(_qn("w:p")))  # document order, table cells included
    header, blocks = _blocks(paragraphs)
    if not blocks:
        raise FillError("Couldn't find the Summary / Skills / Experience headings in your Word resume")

    parsed = parse_resume(resume_text)
    sections = parsed["sections"]
    done = {}
    if "SUMMARY" in blocks:
        done["summary"] = _fill_summary(blocks["SUMMARY"], sections.get("SUMMARY", ""))
    if "TECHNICAL SKILLS" in blocks:
        done["skills"] = _fill_skills(blocks["TECHNICAL SKILLS"], parse_skills(sections.get("TECHNICAL SKILLS", "")))
    for key in ("EXPERIENCE", "PROJECTS"):
        if key in blocks:
            done[key.lower()] = _fill_entries(blocks[key], parse_entries(sections.get(key, "")))
    if not any(done.values()):
        raise FillError("Nothing in your Word resume matched the tailored sections")
    _hide_work_rights(header)
    out_path = Path(out_path)
    doc.save(str(out_path))
    return done


def text_of(path: Path) -> str:
    """The plain text of a Word file (for checks and previews)."""
    from docx import Document
    doc = Document(str(path))
    return "\n".join(_text(p) for p in doc.element.body.iter(_qn("w:p")))

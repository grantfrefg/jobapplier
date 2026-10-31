"""Your own resume or cover letter for a job, used instead of the generated one.

Saved under ~/.applypilot/own_documents and recorded on the job
(own_resume_path, own_cover_path). Applying uploads them in place of the
tailored PDFs, a job with your own resume isn't sent for tailoring, and its
ATS check reads your file. "Use generated" removes them again.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import zipfile
from pathlib import Path

from applypilot import config

KINDS = {"resume": "own_resume_path", "cover": "own_cover_path"}
EXTENSIONS = (".pdf", ".docx")
MAX_BYTES = 10 * 1024 * 1024


class BadDocument(ValueError):
    """Not a file we can upload for you (wrong type, too big, unreadable)."""


def _folder() -> Path:
    return config.APP_DIR / "own_documents"


def _column(kind: str) -> str:
    if kind not in KINDS:
        raise BadDocument("kind must be resume or cover")
    return KINDS[kind]


def check_file(filename: str, data: bytes) -> str:
    """The file's extension (.pdf / .docx) after checking it really is one."""
    ext = Path(filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        raise BadDocument("Use a PDF or Word (.docx) file. For an older .doc, save it as .docx or PDF first.")
    if not data:
        raise BadDocument("That file is empty.")
    if len(data) > MAX_BYTES:
        raise BadDocument("That file is over 10 MB; most application sites won't take it.")
    if ext == ".pdf" and not data.startswith(b"%PDF"):
        raise BadDocument("That file isn't a PDF, although its name ends in .pdf.")
    if ext == ".docx" and not data.startswith(b"PK"):
        raise BadDocument("That file isn't a Word document, although its name ends in .docx.")
    return ext


def save(conn, url: str, kind: str, filename: str, data: bytes) -> Path:
    """Store your file for this job (replacing any earlier one) and record it."""
    column = _column(kind)
    ext = check_file(filename, data)
    if not conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone():
        raise BadDocument("No such job.")
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{hashlib.sha1(url.encode()).hexdigest()[:12]}_{kind}"
    for old in folder.glob(stem + ".*"):
        old.unlink(missing_ok=True)
    path = folder / (stem + ext)
    path.write_bytes(data)
    conn.execute(f"UPDATE jobs SET {column} = ? WHERE url = ?", (str(path), url))
    if kind == "resume":
        _record_ats(conn, url, path)
    conn.commit()
    return path


def clear(conn, url: str, kind: str) -> None:
    """Go back to the generated document."""
    column = _column(kind)
    row = conn.execute(f"SELECT {column} FROM jobs WHERE url = ?", (url,)).fetchone()
    if row and row[0]:
        Path(row[0]).unlink(missing_ok=True)
    conn.execute(f"UPDATE jobs SET {column} = NULL WHERE url = ?", (url,))
    if kind == "resume":  # the generated resume's ATS check is worked out again when shown
        conn.execute("UPDATE jobs SET ats_score = NULL, ats_report = NULL WHERE url = ?", (url,))
    conn.commit()


def _record_ats(conn, url: str, path: Path) -> None:
    from applypilot.scoring import ats
    job = dict(conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())
    text = extract_text(path)
    if not text.strip():  # a scanned image: nothing to check
        conn.execute("UPDATE jobs SET ats_score = NULL, ats_report = ? WHERE url = ?", (json.dumps(
            {"score": None, "unreadable": True}), url))
        return
    original = config.RESUME_PATH.read_text(encoding="utf-8") if config.RESUME_PATH.exists() else ""
    report = ats.check(text, job, original)
    conn.execute("UPDATE jobs SET ats_score = ?, ats_report = ? WHERE url = ?",
                 (report["score"], json.dumps(report), url))


def extract_text(path: Path) -> str:
    """The text of a PDF or Word file ('' if it can't be read, e.g. a scanned image)."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            xml = re.sub(r"<w:tab/>", "\t", xml)
            xml = re.sub(r"<w:br/>", "\n", xml)
            text = re.sub(r"<[^>]+>", "", xml)
            return html.unescape(text).strip()
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages).strip()
    except Exception:
        return ""


def documents(job: dict) -> dict[str, dict]:
    """The resume and cover letter a job uses: {"resume": {...}, "cover": {...}}.

    Each is {"file": Path | None, "text": str, "own": bool}: your file when you
    gave one, else the generated PDF and its text.
    """
    out = {}
    for kind, generated_col in (("resume", "tailored_resume_path"), ("cover", "cover_letter_path")):
        own = job.get(KINDS[kind])
        if own and Path(own).exists():
            out[kind] = {"file": Path(own), "text": extract_text(Path(own)), "own": True}
            continue
        doc = {"file": None, "text": "", "own": False}
        base = job.get(generated_col)
        if base:
            base = Path(base)
            for suffix in (".md", ".txt"):
                if base.with_suffix(suffix).exists():
                    doc["text"] = base.with_suffix(suffix).read_text(encoding="utf-8")
                    break
            if base.with_suffix(".pdf").exists():
                doc["file"] = base.with_suffix(".pdf")
            if kind == "resume" and base.with_suffix(".docx").exists():
                from applypilot.scoring import look
                if look.layout() == "word":  # your Word resume, filled with the tailored content
                    doc["file"] = base.with_suffix(".docx")
        out[kind] = doc
    return out


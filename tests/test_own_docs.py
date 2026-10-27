"""Your own resume / cover letter for a job, instead of the generated one."""

import io
import zipfile

import pytest

from applypilot import config, database, own_docs
from applypilot.apply import local_engine as le

RESUME_TEXT = ("Jane Doe\nSydney, NSW | Australian Citizen\njane@example.com | 0412 345 678\n"
               "SUMMARY\nData scientist.\nSKILLS\nPython, SQL\nEXPERIENCE\nData Scientist, Acme\nMar 2021 - Present\n"
               "- Built models in Python\nEDUCATION\nUNSW, BSc\n")


def make_docx(text: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="w"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def make_pdf(text: str) -> bytes:
    """A one-page PDF with real (selectable) text."""
    lines = " ".join(f"({line.replace('(', '').replace(')', '')}) Tj T*" for line in text.splitlines())
    stream = f"BT /F1 10 Tf 12 TL 40 800 Td {lines} ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "RESUME_PATH", tmp_path / "resume.txt")
    conn = database.init_db(tmp_path / "test.db")
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    (tailored / "acme.txt").write_text("generated resume")
    (tailored / "acme.pdf").write_bytes(b"%PDF-1.4 generated")
    conn.execute("INSERT INTO jobs (url, title, site, fit_score, full_description, tailored_resume_path) "
                 "VALUES ('https://ex.com/1', 'Data Scientist', 'linkedin', 9, 'Python SQL', ?)",
                 (str(tailored / "acme.txt"),))
    conn.execute("INSERT INTO jobs (url, title, site, fit_score, full_description, tailor_status) "
                 "VALUES ('https://ex.com/2', 'Senior Data Scientist', 'indeed', 9, 'Python', 'failed_judge')")
    conn.commit()
    return conn


@pytest.mark.parametrize("name, data, message", [
    ("cv.doc", b"\xd0\xcf\x11\xe0", "save it as .docx or PDF"),
    ("cv.pdf", b"not a pdf", "isn't a PDF"),
    ("cv.docx", b"%PDF-1.4", "isn't a Word document"),
    ("cv.pdf", b"", "empty"),
    ("cv.pdf", b"%PDF" + b"x" * own_docs.MAX_BYTES, "over 10 MB"),
], ids=["old-doc", "fake-pdf", "fake-docx", "empty", "too-big"])  # short names: the 10 MB one would be the test id
def test_only_real_pdf_and_word_files_are_taken(name, data, message):
    with pytest.raises(own_docs.BadDocument, match=message):
        own_docs.check_file(name, data)


def test_text_is_read_from_word_and_pdf(tmp_path):
    docx, pdf = tmp_path / "a.docx", tmp_path / "a.pdf"
    docx.write_bytes(make_docx("Jane Doe\nData Scientist & Engineer"))
    pdf.write_bytes(make_pdf("Jane Doe\nData Scientist"))
    assert own_docs.extract_text(docx) == "Jane Doe\nData Scientist & Engineer"
    assert "Data Scientist" in own_docs.extract_text(pdf)
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 broken")
    assert own_docs.extract_text(tmp_path / "bad.pdf") == ""


def test_own_resume_replaces_the_generated_one_until_cleared(jobs_db):
    url = "https://ex.com/1"
    own_docs.save(jobs_db, url, "resume", "My CV.docx", make_docx(RESUME_TEXT))
    job = dict(jobs_db.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())
    docs = own_docs.documents(job)
    assert docs["resume"]["own"] and docs["resume"]["file"].suffix == ".docx"
    assert docs["resume"]["text"].startswith("Jane Doe")
    assert not docs["cover"]["own"] and docs["cover"]["file"] is None
    assert job["ats_score"] is not None  # the ATS check read your file

    # A second upload replaces the first (even with another file type)
    own_docs.save(jobs_db, url, "resume", "cv.pdf", make_pdf(RESUME_TEXT))
    assert len(list((config.APP_DIR / "own_documents").glob("*_resume.*"))) == 1

    own_docs.clear(jobs_db, url, "resume")
    job = dict(jobs_db.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())
    docs = own_docs.documents(job)
    assert not docs["resume"]["own"] and docs["resume"]["file"].name == "acme.pdf"
    assert docs["resume"]["text"] == "generated resume"
    assert job["ats_score"] is None  # worked out again for the generated resume
    assert not list((config.APP_DIR / "own_documents").glob("*"))


def test_a_scanned_resume_is_flagged_unreadable(jobs_db):
    own_docs.save(jobs_db, "https://ex.com/1", "resume", "scan.pdf", make_pdf(""))
    row = jobs_db.execute("SELECT ats_score, ats_report FROM jobs WHERE url = 'https://ex.com/1'").fetchone()
    assert row[0] is None and '"unreadable": true' in row[1]


def test_own_resume_makes_a_rejected_job_ready_and_skips_tailoring(jobs_db):
    url = "https://ex.com/2"
    pending = {j["url"] for j in database.get_jobs_by_stage(jobs_db, "pending_tailor", min_score=7)}
    assert url in pending
    own_docs.save(jobs_db, url, "resume", "cv.pdf", make_pdf(RESUME_TEXT))
    assert url not in {j["url"] for j in database.get_jobs_by_stage(jobs_db, "pending_tailor", min_score=7)}
    assert database.get_stats(jobs_db)["ready_to_apply"] == 2


def test_applying_uploads_your_files(jobs_db, tmp_path):
    url = "https://ex.com/1"
    own_docs.save(jobs_db, url, "cover", "letter.docx", make_docx("Dear Hiring Manager,\nKind regards,\nJane"))
    job = dict(jobs_db.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone())
    text, files = le._load_documents(job, {"personal": {"full_name": "Jane Doe"}})
    assert files["{{RESUME_PDF}}"].endswith("Jane_Doe_Resume.pdf")  # generated resume
    assert files["{{COVER_LETTER_PDF}}"].endswith("Jane_Doe_Cover_Letter.docx")  # your letter, as Word
    assert text["{{COVER_LETTER_TEXT}}"].startswith("Dear Hiring Manager")
    assert sorted(p.name for p in le.staged_uploads()) == ["Jane_Doe_Cover_Letter.docx", "Jane_Doe_Resume.pdf"]

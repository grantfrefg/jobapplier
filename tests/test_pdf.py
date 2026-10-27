"""Resume and cover letter PDFs: A4, ATS-friendly, Australian conventions."""

import re
from datetime import date

import pytest

from applypilot import config
from applypilot.scoring import pdf
from applypilot.scoring.context import work_rights
from applypilot.scoring.tailor import assemble_resume_text

PROFILE = {"personal": {"full_name": "Jane Doe", "email": "jane@example.com", "phone": "0412 345 678",
                        "city": "Sydney", "province_state": "NSW", "linkedin_url": "https://linkedin.com/in/janedoe"},
           "work_authorization": {"legally_authorized_to_work": "Yes", "require_sponsorship": "No",
                                  "work_permit_type": "Australian citizen"}}
DATA = {"title": "Backend Engineer", "summary": "Backend engineer building Python services on AWS.",
        "skills": {"Languages": "Python, Go"},
        "experience": [{"header": "Software Engineer at Acme Health", "subtitle": "Python, AWS | Mar 2021 - Present",
                        "bullets": ["Built a Python ingestion service"]}],
        "projects": [], "education": "UNSW | Bachelor of Engineering (Software)"}
LETTER = ("Dear Hiring Manager,\n\nAt Acme Health I built a Python ingestion service.\n\n"
          "I also cut reporting lag from 6 hours to 15 minutes.\n\nHappy to walk through any of this.\n\nJane")


@pytest.mark.parametrize("auth, expected", [
    ({"work_permit_type": "Australian citizen"}, "Australian Citizen"),
    ({"work_permit_type": "Permanent resident"}, "Permanent Resident"),
    ({"work_permit_type": "482 visa"}, "482 visa"),
    ({"legally_authorized_to_work": True, "require_sponsorship": False}, "Full working rights"),
    ({"legally_authorized_to_work": "Yes", "require_sponsorship": "Yes"}, ""),
    ({}, ""),
])
def test_work_rights(auth, expected):
    assert work_rights({"work_authorization": auth}) == expected


def test_resume_header_has_location_and_work_rights():
    text = assemble_resume_text(DATA, PROFILE)
    assert text.splitlines()[:4] == [
        "Jane Doe", "Backend Engineer", "Sydney, NSW | Australian Citizen",
        "jane@example.com | 0412 345 678 | https://linkedin.com/in/janedoe"]
    parsed = pdf.parse_resume(text)
    assert parsed["location"] == "Sydney, NSW | Australian Citizen"


@pytest.mark.parametrize("title, subtitle, job, expected", [
    ("Software Engineer at Acme Health", "Python, AWS | Mar 2021 - Present", True,
     ("Software Engineer, Acme Health", "Mar 2021 – Present", "Python, AWS")),
    ("Data Analyst | Sydney Health", "2019 - 2023 | SQL", True, ("Data Analyst, Sydney Health", "2019 – 2023", "SQL")),
    ("Security Engineer at Beta", "Java, ISO 27001 | Feb 2019 - Feb 2021", True,
     ("Security Engineer, Beta", "Feb 2019 – Feb 2021", "Java, ISO 27001")),
    ("Look at Me app", "2022 | React Native", False, ("Look at Me app", "2022", "React Native")),
    ("Engineer at Acme", "Python", True, ("Engineer, Acme", "", "Python")),
])
def test_entries_separate_title_employer_and_dates(title, subtitle, job, expected):
    assert pdf.split_entry(title, subtitle, job) == expected


def test_resume_html_is_ats_friendly():
    html = pdf.build_html(pdf.parse_resume(assemble_resume_text(DATA, PROFILE)))
    assert "size: A4" in html and "<table" not in html and "<img" not in html
    assert "<title>Jane Doe - Resume</title>" in html
    assert '<a href="mailto:jane@example.com">' in html and ">linkedin.com/in/janedoe</a>" in html
    assert '<span class="entry-dates">Mar 2021 – Present</span>' in html
    assert ">Projects<" not in html  # empty section left out
    assert "Sydney, NSW | Australian Citizen" in html


def test_cover_letter_keeps_every_paragraph_and_the_sign_off():
    assert pdf.is_cover_letter(LETTER) and not pdf.is_cover_letter("Jane Doe\nBackend Engineer")
    html = pdf.build_letter_html(LETTER, PROFILE, today=date(2026, 10, 27))
    for line in ("Dear Hiring Manager,", "At Acme Health", "6 hours to 15 minutes", "walk through", "<p>Jane</p>"):
        assert line in html
    assert '<div class="date">27 October 2026</div>' in html
    assert '<div class="name">Jane Doe</div>' in html and "Sydney, NSW" in html
    assert "size: A4" in html


def test_pdfs_are_a4(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setattr(config, "load_profile", lambda: PROFILE)
    resume = tmp_path / "r.txt"
    resume.write_text(assemble_resume_text(DATA, PROFILE))
    letter = tmp_path / "r_CL.txt"
    letter.write_text(LETTER)
    for f in (resume, letter):
        data = pdf.convert_to_pdf(f).read_bytes()
        box = re.search(rb"/MediaBox\s*\[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", data)
        assert box and round(float(box.group(1))) == 595 and round(float(box.group(2))) == 842  # A4 in points


def test_batch_convert_renders_old_pdfs_again_once(tmp_path, monkeypatch):
    tailored, covers = tmp_path / "tailored", tmp_path / "covers"
    tailored.mkdir()
    covers.mkdir()
    monkeypatch.setattr(pdf, "TAILORED_DIR", tailored)
    monkeypatch.setattr(config, "COVER_LETTER_DIR", covers)
    for f in (tailored / "a.txt", tailored / "b_REJECTED.txt", tailored / "a_JOB.txt", covers / "a_CL.txt"):
        f.write_text("x")
    (tailored / "a.pdf").write_bytes(b"old")
    (covers / "a_CL.pdf").write_bytes(b"old")
    done = []
    monkeypatch.setattr(pdf, "convert_to_pdf", lambda f: done.append(f.name))
    assert pdf.batch_convert() == 2
    assert sorted(done) == ["a.txt", "a_CL.txt"]  # old layouts redone; rejected drafts and job texts skipped
    done.clear()
    assert pdf.batch_convert() == 0 and done == []  # only once


def test_pdf_falls_back_when_the_headless_shell_is_missing(monkeypatch):
    """The app's "Install browser" skips Playwright's headless shell (--no-shell)."""
    tried = []

    class Chromium:
        def launch(self, **kwargs):
            tried.append(kwargs)
            if "channel" not in kwargs:
                raise RuntimeError("Executable doesn't exist at .../chrome-headless-shell")
            return "browser"

    class P:
        chromium = Chromium()

    monkeypatch.setattr(config, "find_system_browsers", lambda: [])
    assert pdf._launch_headless(P()) == "browser"
    assert tried == [{"headless": True}, {"headless": True, "channel": "chromium"}]

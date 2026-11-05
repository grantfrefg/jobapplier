"""Resume look (Setup -> Resume look): layout, colour, tagline, tools line and work rights."""

from pathlib import Path

import pytest

from applypilot.scoring import ats, look, pdf


def html():
    return pdf.build_html(pdf.parse_resume(look.SAMPLE))


def test_defaults_are_calm():
    page = html()
    assert ">Senior Data Scientist<" in page and "Production ML" not in page  # no tagline
    assert "Python, PyTorch, BigQuery" not in page  # no tools line under each job
    assert "Australian Citizen" in page and look.ACCENTS["navy"] in page
    assert "text-align: center" not in page.split(".section")[0]  # modern: left-aligned header


def test_each_option_changes_the_page(monkeypatch):
    monkeypatch.setenv("RESUME_TAGLINE", "on")
    monkeypatch.setenv("RESUME_TECH_LINE", "on")
    monkeypatch.setenv("RESUME_ACCENT", "teal")
    page = html()
    assert "Senior Data Scientist · Production ML" in page and "Python, PyTorch, BigQuery" in page
    assert look.ACCENTS["teal"] in page
    built_in = [name for name in look.LAYOUTS if name not in look.OWN_STYLE]
    css = {name: look.resume_css(name) for name in built_in}
    assert len(set(css.values())) == len(built_in) and "serif" in css["classic"]
    monkeypatch.setenv("RESUME_LAYOUT", "nonsense")
    assert look.layout() == "modern"


@pytest.mark.parametrize("mode, permit, shown", [
    ("auto", "Australian Citizen", "Australian Citizen"),
    ("auto", "Permanent Resident", "Permanent Resident"),
    ("auto", "Working holiday visa", ""),
    ("auto", "Student visa", ""),
    ("auto", "Temporary visa with full work rights", "Full working rights"),
    ("always", "Working holiday visa", "Working holiday visa"),
    ("never", "Australian Citizen", ""),
])
def test_work_rights_in_the_header(monkeypatch, mode, permit, shown):
    monkeypatch.setenv("SHOW_WORK_RIGHTS", mode)
    assert look.shown_work_rights(permit) == shown  # with no job (or a permanent one)
    if "working holiday" not in permit.lower():  # that one was decided for its job when tailoring
        assert look.header_details(f"Sydney, NSW | {permit}") == " | ".join(x for x in ("Sydney, NSW", shown) if x)


def test_hidden_work_rights_are_not_an_ats_problem(monkeypatch):
    text = look.SAMPLE.replace(" | Australian Citizen", "")
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "always")
    assert any("Work rights not stated" in i for i in ats.check(text)["issues"])
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "never")
    report = ats.check(text)
    assert not any("Work rights" in i for i in report["issues"])
    assert any("left off" in p for p in report["passed"])


FIXTURE = Path(__file__).parent / "fixtures" / "styled_resume.pdf"  # Georgia-style serif, dark red headings


def test_match_my_resume_reads_the_pdfs_style(monkeypatch):
    from applypilot import config
    from applypilot.scoring import style_match
    style = style_match.extract(FIXTURE)
    assert style["serif"] and style["size"] == 11 and style["page"] == "A4"
    assert style["name"]["colour"] == "#7a1f1f" and style["name"]["centred"] and style["header_centred"]
    heading = style["heading"]
    assert heading["upper"] and heading["bold"] and heading["colour"] == "#7a1f1f" and heading["rule"]
    assert style["bullet"] == "▪"

    # Uploading the PDF keeps its style, and the "mine" layout renders with it
    monkeypatch.setattr(config, "RESUME_PDF_PATH", config.APP_DIR / "resume.pdf")
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    config.RESUME_PDF_PATH.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setenv("RESUME_LAYOUT", "mine")
    page = html()
    assert "#7a1f1f" in page and "text-transform: uppercase" in page and "'▪'" in page
    assert style_match.style_path().exists()


def test_match_my_resume_without_a_pdf_falls_back_to_modern(monkeypatch):
    from applypilot import config
    monkeypatch.setattr(config, "RESUME_PDF_PATH", config.APP_DIR / "missing.pdf")
    monkeypatch.setenv("RESUME_LAYOUT", "mine")
    assert look.resume_css() == look.resume_css("modern")


def test_pdf_font_names_become_css_families():
    from applypilot.scoring.style_match import font_family
    assert font_family("ABCDEF+TimesNewRomanPSMT") == ("Times New Roman", True)
    assert font_family("Calibri-Bold") == ("Calibri", False)
    assert font_family("ArialMT") == ("Arial", False)
    assert font_family("XYZ+Garamond-Italic")[1]


def test_page_target(monkeypatch):
    grad = {"experience": {"years_of_experience_total": "1"}}
    senior = {"experience": {"years_of_experience_total": "8"}}
    assert look.pages_target(grad) == 1 and look.pages_target(senior) == 2 and look.pages_target({}) == 2
    assert "ONE page" in look.length_rule(grad) and "2 pages" in look.length_rule(senior)
    monkeypatch.setenv("RESUME_PAGES", "1")
    assert look.pages_target(senior) == 1
    from applypilot.scoring import tailor
    assert "ONE page" in tailor._build_application_prompt(senior)
    report = ats.check(look.SAMPLE + ("\n- Extra bullet about reporting work for the team" * 60))
    assert any("fits on one page" in i for i in report["issues"])


@pytest.mark.parametrize("title, ad, short", [
    ("Warehouse Assistant", "Casual role, immediate start", True),
    ("Data Analyst (Contract)", "Initial 6 month contract with a view to extend", True),
    ("Admin Officer", "Temp to perm opportunity", True),
    ("Project Coordinator", "12-month fixed-term role covering parental leave", True),
    ("Business Analyst", "Daily rate $650 + super", True),
    ("Data Analyst", "Permanent full-time role. You'll manage supplier contract renewals.", False),
    ("Accountant", "Join our growing team. 3 months probation applies.", False),
    ("Software Engineer", "Hybrid, Sydney CBD, great culture", False),  # unclear: treated as permanent
])
def test_contract_temp_and_casual_ads_are_told_apart(title, ad, short):
    assert look.short_term_role({"title": title, "full_description": ad}) is short


def test_working_holiday_visa_is_shown_per_job(monkeypatch):
    from applypilot.scoring import tailor
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "auto")
    profile = {"personal": {"full_name": "Sam Rivera", "city": "Sydney", "province_state": "NSW"},
               "work_authorization": {"work_permit_type": "Working holiday visa"}}
    data = {"title": "Operations Analyst", "summary": "Analyst.", "skills": {}, "experience": [], "education": "Uni"}
    contract = {"title": "Operations Analyst", "full_description": "3 month contract, immediate start"}
    permanent = {"title": "Operations Analyst", "full_description": "Permanent full-time position"}
    assert "Sydney, NSW | Working holiday visa" in tailor.assemble_resume_text(data, profile, contract)
    text = tailor.assemble_resume_text(data, profile, permanent)
    assert "Working holiday" not in text and "Sydney, NSW" in text
    # Rendering keeps what tailoring chose; the ATS check doesn't ask for it on the permanent one
    assert look.header_details("Sydney, NSW | Working holiday visa") == "Sydney, NSW | Working holiday visa"
    from applypilot import config
    monkeypatch.setattr(config, "load_profile", lambda: profile)
    assert look.work_rights_left_off(permanent) and not look.work_rights_left_off(contract)
    assert look.shown_work_rights("Student visa", job=contract) == ""  # other limited visas stay off


def test_job_kind_reads_the_ad():
    from applypilot.scoring.look import job_kind
    kind = lambda title, body="", salary="": job_kind({"title": title, "description": body, "salary": salary})  # noqa: E731
    assert kind("Data Analyst - 6 month contract") == "Fixed term"
    assert kind("Data Analyst", "Initial 12-month fixed-term role") == "Fixed term"
    assert kind("Data Analyst (Contract)") == "Contract"
    assert kind("Data Engineer", "Outside IR35, London", "£500 per day") == "Contract"
    assert kind("Payroll Officer", "Temp to perm opportunity") == "Temp to perm"
    assert kind("Admin Assistant - Temp") == "Temp"
    assert kind("Barista", "Casual role, weekend shifts") == "Casual"
    assert kind("Analyst", "Permanent role. Smart casual dress, contract management skills") == "Permanent"
    assert kind("Analyst", "Join our team. Casual dress Fridays.") == "Not stated"
    assert kind("Analyst", "Applicants need temporary residence visas") == "Not stated"
    assert job_kind(None) == "Not stated"


def test_job_kind_uses_the_boards_job_type():
    """The board's own type (JobSpy, Reed) fills in when the ad doesn't say, without overriding a clearer ad."""
    from applypilot.scoring.look import job_kind, short_term_role
    job = lambda title, body="", board=None: {"title": title, "description": body, "board_job_type": board}  # noqa: E731
    assert job_kind(job("Data Analyst", "Hybrid, Sydney CBD", "fulltime,contract")) == "Contract"
    assert job_kind(job("Data Analyst", "Hybrid, Sydney CBD", "temporary")) == "Temp"
    assert job_kind(job("Data Analyst", "Hybrid, Sydney CBD", "permanent,fulltime")) == "Permanent"
    assert job_kind(job("Data Analyst", "Hybrid, Sydney CBD", "fulltime")) == "Not stated"  # says nothing either way
    assert job_kind(job("Data Analyst", "Initial 6 month contract", "contract")) == "Fixed term"  # the ad is clearer
    assert job_kind(job("Data Analyst", "Permanent role, contract management", "contract")) == "Contract"
    assert job_kind(job("Data Analyst (Permanent)", "", "contract")) == "Permanent"  # the title wins
    assert job_kind(job("Data Analyst", "Casual shifts", "permanent")) == "Casual"
    # ...and so does the working holiday visa on the resume, which follows the job type
    assert short_term_role(job("Data Analyst", "Hybrid, Sydney CBD", "contract"))
    assert not short_term_role(job("Data Analyst", "Hybrid, Sydney CBD", "fulltime"))
    assert short_term_role(job("Analyst", "A 6-month project to migrate our data"))

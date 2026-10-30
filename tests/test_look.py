"""Resume look (Setup -> Resume look): layout, colour, tagline, tools line and work rights."""

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
    css = {name: look.resume_css(name) for name in look.LAYOUTS}
    assert len(set(css.values())) == len(look.LAYOUTS) and "serif" in css["classic"]
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
    assert look.shown_work_rights(permit) == shown
    assert look.header_details(f"Sydney, NSW | {permit}") == " | ".join(x for x in ("Sydney, NSW", shown) if x)


def test_hidden_work_rights_are_not_an_ats_problem(monkeypatch):
    text = look.SAMPLE.replace(" | Australian Citizen", "")
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "always")
    assert any("Work rights not stated" in i for i in ats.check(text)["issues"])
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "never")
    report = ats.check(text)
    assert not any("Work rights" in i for i in report["issues"])
    assert any("left off" in p for p in report["passed"])

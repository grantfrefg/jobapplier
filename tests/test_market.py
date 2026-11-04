"""Australia and the UK (market.py): every prompt follows the market, Australia stays as it was."""

import pytest

from applypilot import config, market
from applypilot.apply import local_engine, prompt
from applypilot.scoring import cover_letter, scorer, tailor

PROFILE = {"personal": {"full_name": "Sam Rivera", "city": "London", "phone": "07700 900123"},
           "experience": {"current_job_title": "Data Analyst", "years_of_experience_total": "4"},
           "compensation": {"salary_expectation": "55000", "salary_range_min": "50000"},
           "work_authorization": {"legally_authorized_to_work": "Yes"}, "availability": {}}


@pytest.fixture
def uk(monkeypatch):
    monkeypatch.setattr(config, "load_search_config", lambda: {"country": "uk"})
    return market.UK


def test_the_market_comes_from_the_search_then_the_profile(monkeypatch):
    monkeypatch.setattr(config, "load_search_config", lambda: {"country": "USA"})
    assert market.current({}) is market.AU
    assert market.current({"personal": {"country": "United Kingdom"}}) is market.UK
    monkeypatch.setattr(config, "load_search_config", lambda: {"country": "uk"})
    assert market.current({"personal": {"country": "Australia"}}) is market.UK


def test_australian_prompts_are_unchanged():
    assert scorer.score_prompt(market.AU) == scorer.SCORE_PROMPT
    assert market.localise(local_engine._SYSTEM_PROMPT, local_engine._UK_SWAPS, market.AU) == \
        local_engine._SYSTEM_PROMPT


def test_uk_prompts_lose_every_australian_detail(uk):
    texts = {
        "scorer": scorer.score_prompt(),
        "form": local_engine._system_prompt(),
        "tailor": tailor._build_application_prompt(PROFILE),
        "salary": prompt._build_salary_section(PROFILE),
        "screening": prompt._build_screening_section(PROFILE),
    }
    for name, text in texts.items():
        assert "Australia" not in text and "super " not in text, name
    assert "recruiter in the UK" in texts["scorer"] and "IR35" in texts["scorer"]
    assert "British English" in texts["form"] and "share code" in texts["form"]
    assert "rewriting a CV" in texts["tailor"] and "Currency is GBP" in texts["tailor"]
    assert "competency-based" in cover_letter.cover_letter_rules(PROFILE)
    assert "£50000" in texts["salary"] and "37.5-hour week" in texts["salary"] and "$" not in texts["salary"]


def test_a_missing_passage_is_caught():
    with pytest.raises(ValueError):
        market.localise("nothing Australian here", [("recruiter in Australia", "x")], market.UK)


def test_uk_phone_numbers():
    assert local_engine._international_phone("07700 900123", "United Kingdom") == "+44 7700 900123"
    assert local_engine._international_phone("0412 345 678", "Australia") == "+61 412 345 678"


@pytest.mark.parametrize("permit, header", [
    ("British citizen", "British Citizen"),
    ("Irish citizen", "Irish Citizen"),
    ("Settled status (ILR)", "Right to work in the UK"),
    ("Pre-settled status", "Right to work in the UK"),
    ("Graduate visa", "Right to work in the UK"),
    ("Other visa with full right to work", "Right to work in the UK"),
    ("Skilled Worker visa (needs sponsorship)", ""),  # Automatic leaves it off
    ("Student visa", ""),
])
def test_uk_work_rights_in_the_header(monkeypatch, permit, header):
    from applypilot.scoring import look
    from applypilot.scoring.context import work_rights
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "auto")
    assert look.shown_work_rights(work_rights({"work_authorization": {"work_permit_type": permit}})) == header


def test_youth_mobility_is_shown_per_job_like_a_working_holiday_visa(monkeypatch, uk):
    from applypilot.scoring import look
    from applypilot.scoring.context import candidate_brief, work_rights
    monkeypatch.setenv("SHOW_WORK_RIGHTS", "auto")
    line = work_rights({"work_authorization": {"work_permit_type": "Youth Mobility Scheme visa"}})
    contract = {"title": "Analyst", "full_description": "6 month contract, outside IR35"}
    assert look.shown_work_rights(line, job=contract) == "Youth Mobility Scheme visa"
    assert look.shown_work_rights(line, job={"title": "Analyst", "full_description": "Permanent role"}) == ""
    brief = candidate_brief({**PROFILE, "work_authorization": {"work_permit_type": "Youth Mobility Scheme visa"}})
    assert "up to 2 years" in brief and "GBP base, excluding pension" in brief


def test_the_profile_form_offers_the_markets_options(uk):
    from applypilot import profile_options as po
    shown = po.options_for(market.UK)
    assert "Youth Mobility Scheme visa" in shown["work_authorization.work_permit_type"]
    assert "Australian citizen" not in shown["work_authorization.work_permit_type"]
    assert shown["personal.province_state"][0] == "England" and "SC" in shown["work_authorization.security_clearance"]
    assert "eeo_voluntary.aboriginal_or_torres_strait_islander" not in shown
    assert "NSW" in po.options_for(market.AU)["personal.province_state"]
    assert po.normalise("work_authorization.work_permit_type", "ILR") == "Settled status (ILR)"


@pytest.mark.parametrize("place, kept", [
    ("London, England, United Kingdom", True),
    ("City of London", True),
    ("Croydon, England, United Kingdom", True),
    ("Canary Wharf", True),
    ("United Kingdom (Remote)", True),
    ("Manchester, England, United Kingdom", False),
    ("Victoria, Australia", False),
])
def test_a_london_search_keeps_greater_london(place, kept):
    accept = config.location_accept_patterns("London")
    assert config.location_ok(place, accept, []) is kept


def test_uk_employer_lines_are_recognised():
    from applypilot.scoring import facts
    source = facts.Source("Jane Doe\nExperience\nData Analyst, Monzo Bank, London\n- Built dashboards\n")
    assert source.names_employer("Monzo Bank")


def test_uk_ats_check_and_cv_naming(uk, monkeypatch):
    from applypilot.scoring import ats, look, pdf
    text = look.SAMPLE.replace("Wentworthville, NSW | Australian Citizen", "London, England | Right to work in the UK")
    report = ats.check(text.replace("0412 345 678", "07700 900123"))
    assert not any("city" in i.lower() or "phone" in i.lower() for i in report["issues"])
    assert "British English spelling" in report["passed"]
    assert any("UK employers" in p for p in report["passed"])
    assert "<title>Jane Morgan - CV</title>" in pdf.build_html(pdf.parse_resume(text))

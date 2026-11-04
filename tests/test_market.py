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

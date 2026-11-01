"""Contract and temp work (e.g. on a working holiday visa): search filter, apply rules, scoring."""

import pandas as pd

from applypilot.apply import prompt
from applypilot.discovery import jobspy
from applypilot.scoring.context import candidate_brief
from applypilot.wizard.init import build_search_yaml


def test_contract_searches_ask_for_contract_and_temp_listings(monkeypatch):
    calls = []

    def fake(kwargs, max_retries=2):
        calls.append(kwargs.get("job_type"))
        return pd.DataFrame([{"job_url": f"https://ex.com/{kwargs.get('job_type')}"}])
    monkeypatch.setattr(jobspy, "_scrape_with_retry", fake)
    df = jobspy._scrape_types({"search_term": "Analyst"}, "contract", 0)
    assert calls == ["contract", "temporary"] and len(df) == 2
    calls.clear()
    jobspy._scrape_types({"search_term": "Analyst"}, "", 0)
    assert calls == [None]  # any type: one unfiltered search
    assert 'job_type: "contract"' in build_search_yaml("Sydney, NSW", ["Analyst"], job_type="contract")
    assert "job_type" not in build_search_yaml("Sydney, NSW", ["Analyst"])


def test_rate_questions_follow_the_profile():
    def rule(answer):
        return prompt._rate_rule({"availability": {"available_for_contract": answer}})
    assert "hourly or day rate question is fine" in rule("Yes") and "NEVER agree to hourly" in rule("No")


def test_working_holiday_visa_shapes_the_score():
    whv = {"work_authorization": {"work_permit_type": "Working holiday visa"},
           "availability": {"available_for_contract": "Yes", "available_for_full_time": "No"}}
    brief = candidate_brief(whv)
    assert "6 months with one employer" in brief and "wants contract" in brief and "not full-time" in brief
    assert "says nothing about visas" in brief and "silence is not a dealbreaker" in brief
    assert "6 months" not in candidate_brief({"work_authorization": {"work_permit_type": "Australian citizen"}})

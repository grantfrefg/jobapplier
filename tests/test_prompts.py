"""The AI prompts: engineer-first, Australian conventions, honest by default."""

import re
from datetime import datetime

from applypilot.apply import local_engine as le
from applypilot.apply import prompt as apply_prompt
from applypilot.scoring import cover_letter, scorer, tailor
from applypilot.scoring.context import candidate_brief

PROFILE = {
    "personal": {"full_name": "Jane Doe", "preferred_name": "Jane", "city": "Sydney", "province_state": "NSW"},
    "work_authorization": {"legally_authorized_to_work": "Yes", "require_sponsorship": "No",
                           "work_permit_type": "Permanent resident", "security_clearance": ""},
    "availability": {"willing_to_relocate": "No"},
    "compensation": {"salary_range_min": "120000", "salary_currency": "AUD"},
    "experience": {"target_role": "Backend Engineer", "years_of_experience_total": "4"},
    "skills_boundary": {"languages": ["Python", "Go"], "cloud": ["AWS"]},
    "resume_facts": {"real_metrics": ["2M events a day"]},
}


def test_scoring_sees_candidate_dealbreakers(monkeypatch):
    sent = {}

    class Fake:
        def chat_json(self, messages, **kw):
            sent["messages"] = messages
            return {"results": [{"id": "j1", "score": 3, "keywords": "Python", "reasoning": "Needs NV1."}]}

    monkeypatch.setattr(scorer, "get_client", lambda: Fake())
    job = {"url": "u", "title": "Backend Engineer", "full_description": "NV1 clearance required."}
    assert scorer.score_batch("resume", [job], PROFILE)["u"]["score"] == 3
    system, user = sent["messages"][0]["content"], sent["messages"][1]["content"]
    assert "Security clearance" in system and "DEALBREAKERS" in system
    assert "Security clearance held: none" in user and "Permanent resident" in user
    assert "Target role: Backend Engineer, about 4 years" in user


def test_candidate_brief_without_profile_details():
    assert "Target role: engineer" in candidate_brief({})


def test_tailoring_never_invites_new_tools():
    rules = tailor._tailor_rules(PROFILE)
    assert "MAY add" not in rules and "Never add a tool" in rules
    assert "Australian English" in rules and "2 pages" in rules
    assert "Every bullet must be reworded" not in rules
    judge = tailor._build_judge_prompt(PROFILE)
    assert "LEARNABLE" not in judge and "even if it's closely related" in judge


def test_cover_letter_rules_are_role_aware_and_honest_about_the_company():
    profile = {**PROFILE, "experience": {**PROFILE["experience"], "current_job_title": "Software Engineer"}}
    rules = cover_letter.cover_letter_rules(profile)
    # The letter knows their real title, never the target role as if it were theirs
    assert "currently a Software Engineer, 4 years of experience" in rules
    assert "a Backend Engineer" not in rules
    assert "Never call the candidate by a job title they haven't held" in rules
    assert "Never invent facts about the company" in rules
    assert "Australian English" in rules


def test_scoring_keywords_feed_the_writing_prompt():
    text = cover_letter.job_text({"title": "Backend Engineer", "score_reasoning": "Python, AWS Lambda\nStrong fit."})
    assert text.endswith("MUST-HAVES THE RESUME SHOWS (lead with these): Python, AWS Lambda")
    assert "MUST-HAVES" not in cover_letter.job_text({"title": "x"})


def test_form_prompt_uses_australian_dates_and_flags_legal_questions():
    prompt = le._build_user_prompt({"title": "Backend Engineer"}, [], {}, "", False, False, None)
    assert f"TODAY: {datetime.now():%d/%m/%Y}" in prompt
    assert "DD/MM/YYYY" in le._SYSTEM_PROMPT
    assert re.search(r"Criminal history.*DON'T answer", le._SYSTEM_PROMPT, re.S)
    assert "yes only if the resume or skills show X" in le._SYSTEM_PROMPT


def test_claude_code_screening_is_truthful():
    section = apply_prompt._build_screening_section({**PROFILE, "personal": {"city": "Sydney"}})
    assert "answer YES" not in section and "learn tools fast" not in section
    assert "willing to relocate: No" in section and "Security clearance: none held" in section

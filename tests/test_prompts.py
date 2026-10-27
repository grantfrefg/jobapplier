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
    assert "LEARNABLE" not in judge and "even a closely related one" in judge


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



def test_fact_check_uses_the_resume_and_confirmed_skills_and_must_quote():
    unconfirmed = {**PROFILE, "skills_boundary": {"tools": ["Docker", "Kafka"]}}  # Docker is a template example
    judge = tailor._build_judge_prompt(unconfirmed)
    assert "ORIGINAL RESUME: every tool, skill, number" in judge  # resume skills and numbers always count
    assert "extra skills the candidate listed in their profile: Kafka" in judge and "Docker" not in judge
    assert "real metrics" not in judge.lower()  # numbers come from the resume, not a short profile list
    assert "When in doubt, FAIL" not in judge and "quote a specific phrase" in judge
    assert "never fail them" in judge  # statements about the employer from the ad
    assert "other sections" not in judge  # no moving achievements between roles


def test_fact_check_sees_the_job_ad(monkeypatch):
    sent = {}

    class Fake:
        def chat_json(self, messages, **kw):
            sent["user"] = messages[1]["content"]
            return {"verdict": "PASS", "issues": "none"}

    monkeypatch.setattr(tailor, "get_client", lambda: Fake())
    result = tailor.judge_tailored_resume("orig", "new", "Backend Engineer", PROFILE, "Dear...", job_ad="Kafka team")
    assert result["passed"] and "JOB AD (excerpt):\nKafka team" in sent["user"]


def test_letter_may_quote_numbers_from_the_ad():
    from applypilot.scoring import facts
    source = facts.source_for("Built APIs for 2M users", PROFILE)
    letter = "Dear Hiring Manager,\n\nYour team of 40 engineers ships weekly. I built APIs for 2M users."
    assert tailor.letter_problems(letter, source, "Built APIs for 2M users", "A team of 40 engineers.") == []
    assert "40" in " ".join(tailor.letter_problems(letter, source, "Built APIs for 2M users"))


def test_form_prompt_has_the_ad_the_board_and_super():
    job = {"title": "Backend Engineer", "site": "linkedin", "full_description": "You'll own the payments API."}
    prompt = le._build_user_prompt(job, [], {}, "", False, False, None)
    assert "FOUND ON: LinkedIn" in prompt and "JOB DESCRIPTION (excerpt):\nYou'll own the payments API." in prompt
    assert "excluding superannuation" in le._SYSTEM_PROMPT and "12% super" in le._SYSTEM_PROMPT
    assert "How did you hear about this job?" in le._SYSTEM_PROMPT and "referred" in le._SYSTEM_PROMPT


def test_scoring_knows_work_type_and_pay():
    profile = {**PROFILE, "availability": {"available_for_full_time": "Yes", "available_for_contract": "No"}}
    brief = candidate_brief(profile)
    assert "Work type: wants full-time; not contract" in brief
    assert "Minimum salary: 120000 AUD base, excluding super" in brief
    assert "contract-only" in scorer.SCORE_PROMPT and "score 5 at most" in scorer.SCORE_PROMPT


def test_writer_uses_the_ads_form_and_the_letter_knows_the_situation():
    assert "use the ad's form" in tailor._tailor_rules(PROFILE)
    profile = {**PROFILE, "work_authorization": {"require_sponsorship": "Yes"},
               "availability": {"willing_to_relocate": "Yes", "notice_period": "4 weeks"}}
    rules = cover_letter.cover_letter_rules(profile)
    assert "needs visa sponsorship" in rules and "will relocate" in rules and "notice period: 4 weeks" in rules
    plain = cover_letter.cover_letter_rules(PROFILE)
    assert "sponsorship" not in plain and "relocate" not in plain


def test_claude_code_salary_is_australian():
    section = apply_prompt._build_salary_section({"compensation": {"salary_expectation": "120000",
                                                                   "salary_range_max": "140000"}})
    assert "AUD BASE (excluding superannuation)" in section and "x 1.12" in section
    assert "2080" not in section and "$110K" not in section and "$60/hr" in section

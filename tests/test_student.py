"""Student visas (student.py): the hours limit, the course end and the visa after it, for the scorer,
the form filler and the Claude Code / Codex prompt, in Australia and the UK."""

from datetime import date

import pytest

from applypilot import market, student
from applypilot.apply import local_engine as le
from applypilot.apply import prompt
from applypilot.scoring import context

TODAY = date(2026, 11, 11)


def profile(permit="Student visa", end="11/2027", level="Bachelor's or coursework Master's",
            after="Temporary Graduate visa (subclass 485)", country="Australia"):
    return {"personal": {"full_name": "Priya Raman", "city": "Kensington", "country": country},
            "work_authorization": {"work_permit_type": permit, "course_end": end, "study_level": level,
                                   "post_study_visa": after, "legally_authorized_to_work": "Yes",
                                   "require_sponsorship": "No"},
            "experience": {"target_role": "Data Analyst"}, "availability": {}, "compensation": {}}


@pytest.mark.parametrize("text, end", [("11/2027", date(2027, 11, 30)), ("2027-02", date(2027, 2, 28)),
                                       ("Nov 2027", date(2027, 11, 30)), ("June 2027", date(2027, 6, 30)),
                                       ("12/2026", date(2026, 12, 31)), ("", None), ("soon", None), ("13/2027", None)])
def test_the_course_end_is_read_in_any_common_form(text, end):
    assert student.course_end(profile(end=text)) == end


def test_the_stage_follows_the_course_end():
    assert student.stage(profile(permit="Australian citizen"), TODAY) == ""
    assert student.stage(profile(end="11/2027"), TODAY) == "studying"
    assert student.stage(profile(end="02/2027"), TODAY) == "finishing"
    assert student.stage(profile(end="06/2026"), TODAY) == "graduated"
    assert student.stage(profile(end=""), TODAY) == "studying"  # no end date: assume still studying


def test_australian_hours_and_scoring():
    lines = " ".join(student.scoring_lines(profile(), market.AU, TODAY))
    assert "48 hours a fortnight" in lines and "unlimited during official breaks" in lines
    assert "score it 4 at most" in lines and "November 2027" in lines
    assert "Temporary Graduate visa (subclass 485)" in lines and "permanent residency" in lines
    research = " ".join(student.scoring_lines(profile(level="Research Master's or PhD"), market.AU, TODAY))
    assert "no work-hours limit" in research and "48 hours" not in research


def test_uk_hours_and_scoring():
    uk = profile(level="Degree level", after="Graduate visa", country="United Kingdom", end="06/2027")
    lines = " ".join(student.scoring_lines(uk, market.UK, TODAY))
    assert "20 hours a week in term time" in lines and "can't fill a permanent full-time vacancy" in lines
    assert "dealbreaker" in lines and "settled status" in lines and "permanent residency" not in lines
    below = profile(level="Below degree level", country="United Kingdom")
    assert "10 hours a week" in student.hours_limit(below, market.UK)


def test_after_the_course():
    done = " ".join(student.scoring_lines(profile(end="06/2026"), market.AU, TODAY))
    assert "now on a Temporary Graduate visa" in done and "Full-time and permanent roles fit" in done
    unclear = " ".join(student.scoring_lines(profile(end="06/2026", after="None or not sure"), market.AU, TODAY))
    assert "unclear" in unclear and "part-time and casual" in unclear


def test_form_answers_are_exact_and_never_overstate():
    lines = " ".join(student.form_lines(profile(), market.AU, TODAY))
    assert "Unrestricted / full work rights: no" in lines and "48 hours a fortnight" in lines
    assert "Never say they're a citizen or permanent resident" in lines
    done = " ".join(student.form_lines(profile(end="06/2026"), market.AU, TODAY))
    assert "Full work rights: yes" in done and "'permanent resident or citizen?' is no" in done
    assert student.form_lines(profile(permit="Permanent resident"), market.AU, TODAY) == []


def test_the_scorer_form_filler_and_agent_prompt_all_get_them():
    p = profile()
    assert "48 hours a fortnight" in context.candidate_brief(p)
    assert "48 hours a fortnight" not in context.candidate_brief(profile(permit="Permanent resident"))
    built = le._build_user_prompt({"title": "Data Analyst"}, [], {}, "resume", False, False, None,
                                  work_rights=le._work_rights(p))
    assert "WORK RIGHTS (answer work-rights questions exactly from this)" in built and "48 hours" in built
    assert "WORK RIGHTS" not in le._build_user_prompt({"title": "x"}, [], {}, "r", False, False, None,
                                                      work_rights=le._work_rights(profile(permit="")))
    assert "48 hours a fortnight" in prompt._build_hard_rules(p)


def test_the_resume_header_after_graduating():
    assert context.work_rights(profile(end="06/2026")) == "Full work rights"
    assert context.work_rights(profile()) == "Student visa"  # left off the resume header by the "auto" setting

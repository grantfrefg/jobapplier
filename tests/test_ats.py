"""ATS check, Australian spelling and AI-sounding phrasing."""

import copy

from applypilot.scoring import ats, tailor
from applypilot.scoring.validator import ai_phrasing, australianise, banned_in, validate_cover_letter

ORIGINAL = """Jane Doe
Senior Data Scientist · Jan 2021 – Present, Acme Health
• Built recommender systems in Python and SQL on Google Cloud (BigQuery), with A/B test design.
• Machine learning models for personalization at Acme Health.
"""
GOOD = """Jane Doe
Senior Data Scientist · Production ML
Sydney, NSW | Australian Citizen
jane@example.com | 0412 345 678

SUMMARY
Senior data scientist building machine learning systems in Python and SQL.

TECHNICAL SKILLS
Languages: Python, SQL
Cloud: Google Cloud (BigQuery)

EXPERIENCE
Senior Data Scientist at Acme Health
Jan 2021 - Present | Python, BigQuery
- Built recommender systems in Python and SQL on BigQuery
- Ran A/B tests for personalisation models

EDUCATION
UNSW | Master of Data Science | 2018
""" + "\\n- Extra detail on recommender work in Python." * 30
JOB = {"title": "Senior Data Scientist", "full_description": "Python, SQL, BigQuery and Snowflake. Tableau a plus.",
       "score_reasoning": "Python, SQL, machine learning\nGood fit."}


def test_clean_resume_scores_well():
    report = ats.check(GOOD, JOB, ORIGINAL)
    assert report["score"] >= 90, report
    assert report["missing_have"] == [] and set(report["missing_gap"]) == {"snowflake", "tableau"}
    assert report["keyword_match"] == 100   # gaps the candidate doesn't have don't count against them


def test_parsing_and_knockout_problems_are_reported():
    bad = GOOD.replace("Sydney, NSW | Australian Citizen\n", "").replace("Jan 2021 - Present | ", "")
    bad = bad.replace("machine learning", "ML").replace("personalisation", "personalization")
    report = ats.check(bad, JOB, ORIGINAL)
    issues = " ".join(report["issues"])
    assert "Work rights not stated" in issues and "No city and state" in issues
    assert "No date range" in issues
    assert "missing here: machine learning" in issues
    assert "American spelling: personalization" not in issues  # their own resume spells it that way
    assert report["score"] < 70


def test_australian_spelling_keeps_the_candidates_own_words():
    assert australianise("Optimized models and analyzed behavior.") == "Optimised models and analysed behaviour."
    assert australianise("Center for Data Science", original="Center for Data Science") == "Center for Data Science"


def test_ai_phrasing_and_banned_words():
    text = "I am thrilled to apply! This is not just a job but a calling. I leverage data."
    assert {"exclamation marks", "'not just X but Y'", "gushing ('I'm thrilled')"} <= set(ai_phrasing(text))
    assert "leverage" in banned_in(text)
    assert banned_in("Built a robust pipeline", allowed_text="My robust pipeline") == []  # their own word
    letter = "Dear Hiring Manager,\n\nI am writing to express my interest. I leverage Python!\n\nKind regards,\nJane"
    errors = " ".join(validate_cover_letter(letter)["errors"])
    assert "Banned words: leverage" in errors and "AI-written" in errors


def test_projects_are_optional_and_real_keywords_are_added():
    from test_writing import GOOD_RESUME, PROFILE, RESUME
    resume = copy.deepcopy(GOOD_RESUME)
    resume["projects"] = []
    resume["skills"] = {"Languages": "Python, SQL"}  # the AI dropped Power BI, a must-have Jane has
    job = {"title": "Data Analyst", "full_description": "SQL and Tableau", "score_reasoning": "SQL, Power BI\n"}
    out = tailor.check_facts(resume, "", RESUME, PROFILE, job)
    assert out["errors"] == []
    assert out["resume"]["skills"]["Key skills"] == "Power BI"
    assert "Tableau" not in str(out["resume"]["skills"])  # in the ad, but Jane doesn't have it
    assert tailor.validate_json_fields(out["resume"], PROFILE)["passed"]

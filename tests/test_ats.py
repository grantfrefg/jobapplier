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
    assert "'machine learning' (here: 'ml')" in issues  # another form of the ad's word: half credit
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


def test_other_forms_of_a_term_count_half_and_say_which_word_to_use():
    job = {"title": "Data Scientist", "full_description": "Amazon Web Services and SQL."}
    exact = ats.check(GOOD.replace("Google Cloud (BigQuery)", "Amazon Web Services"), job, ORIGINAL)
    short = ats.check(GOOD.replace("Google Cloud (BigQuery)", "AWS"), job, ORIGINAL)
    assert exact["other_form"] == {} and exact["keyword_match"] == 100
    assert short["other_form"] == {"amazon web services": "aws"} and short["keyword_match"] == 50
    assert any("'amazon web services' (here: 'aws')" in i for i in short["issues"])


def test_characters_a_parser_may_misread():
    assert ats.misread_characters("\ufb01nance team \ue001 (cid:12) ok") == ["\ufb01", "U+E001", "(cid:12)"]
    report = ats.check(GOOD.replace("Built recommender", "Built \ufb01ne recommender"), JOB, ORIGINAL)
    assert any(i.startswith("Characters a parser may misread") for i in report["issues"])


# --- reading the PDF back ---------------------------------------------------

def _pdf(tmp_path, text):
    from test_own_docs import make_pdf
    path = tmp_path / "r.pdf"
    path.write_bytes(make_pdf(text))
    return path


def test_pdf_that_reads_back_correctly(tmp_path):
    issues, passed = ats.pdf_readback(_pdf(tmp_path, GOOD), GOOD)
    assert issues == [] and passed and passed[0].startswith("PDF reads back correctly")


def test_pdf_readback_finds_what_a_parser_would_lose(tmp_path):
    # Name last, no email, Education printed before Experience, a job without its dates
    scrambled = GOOD.replace("Jane Doe\n", "", 1).replace("jane@example.com", "").replace("Jan 2021 - Present | ", "")
    scrambled = scrambled.replace("EXPERIENCE\n", "EDUCATION\nUNSW\n\nEXPERIENCE\n", 1)
    scrambled = scrambled.replace("\nEDUCATION\nUNSW | Master", "\nUNSW | Master") + "\nJane Doe\n"
    issues, passed = ats.pdf_readback(_pdf(tmp_path, scrambled), GOOD)
    text = " ".join(issues)
    assert "your name isn't the first thing" in text
    assert "email address can't be read" in text
    assert "different order" in text
    assert "may not link these jobs to their dates: Senior Data Scientist, Acme Health" in text
    assert passed == []


def test_pdf_with_no_text_is_flagged(tmp_path):
    issues, _ = ats.pdf_readback(_pdf(tmp_path, ""), GOOD)
    assert issues and "no readable text" in issues[0]
    report = ats.check(GOOD, JOB, ORIGINAL, pdf_path=tmp_path / "r.pdf")
    assert report["score"] < ats.check(GOOD, JOB, ORIGINAL)["score"]  # counts against the score


def test_watchlist_skills_are_only_fabricated_when_the_resume_lacks_them():
    from test_writing import GOOD_RESUME, PROFILE
    resume = copy.deepcopy(GOOD_RESUME)
    resume["skills"] = {"Languages": "Python, C++, SQL", "Other": "Scalable systems, Trust and safety"}
    invented = tailor.validate_json_fields(resume, PROFILE, original_text="Python and SQL")
    assert invented["errors"] == ["Fabricated skill: 'c++'"]  # not "scala" or "rust"
    real = tailor.validate_json_fields(resume, PROFILE, original_text="Built tools in C++ and Python")
    assert real["passed"]
    from_profile = {**PROFILE, "skills_confirmed": True, "skills_boundary": {"languages": ["C++"]}}
    assert tailor.validate_json_fields(resume, from_profile, original_text="Python")["passed"]

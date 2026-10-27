"""Fact checks: a tailored resume or letter claims nothing that isn't in the real resume."""

import copy

import pytest

from applypilot.scoring import facts, tailor

RESUME = """Grant Feng
Profile
Hands-on technical leader with 7 years at Woolworths Group, taking enterprise ML systems from design to
production at national scale. Builds on Google Cloud (Vertex AI, BigQuery). Master of Data Science.
Experience
Woolworths Group (WooliesX)
Senior Data Scientist · Jan 2021 – Present
• Technical lead for production recommender systems reaching 10 million customers.
• Rebuilt the recommendation pipelines, cutting running costs by 50%.
• Built two-tower recommendation models on LLM embeddings with PyTorch.
• Partner with engineers across Digital Customer Experience.
Data Scientist · Sep 2019 – Dec 2020
• A/B test design and offline model evaluation in Python and SQL.
Projects
• Side project: TypeScript API with PostgreSQL via Prisma.
Education
University of Technology Sydney, Master of Data Science
"""
PROFILE = {
    "personal": {"full_name": "Grant Feng"},
    "experience": {"current_job_title": "Senior Data Scientist", "target_role": "Lead Data Scientist"},
    # The template's example skills (never treated as real) plus one real extra
    "skills_boundary": {"languages": ["Python", "SQL", "JavaScript"], "devops": ["Docker", "AWS", "CI/CD"],
                        "databases": ["PostgreSQL", "MongoDB"], "tools": ["Git", "Linux"], "ml": ["XGBoost"]},
}
GOOD = {
    "title": "Senior Data Scientist",
    "focus": "Production ML & LLM Systems",
    "summary": "Senior data scientist with 7 years taking ML systems to production at national scale.",
    "skills": {"Languages": "Python, SQL", "ML": "PyTorch, XGBoost", "Cloud": "Google Cloud (Vertex AI, BigQuery)"},
    "experience": [{"header": "Senior Data Scientist at Woolworths Group (WooliesX)",
                    "subtitle": "Jan 2021 - Present | Python, Vertex AI",
                    "bullets": ["Rebuilt the recommendation pipelines, cutting running costs by 50%"]}],
    "projects": [],
    "education": "University of Technology Sydney | Master of Data Science",
}


def check(resume=None, letter=""):
    return tailor.check_facts(copy.deepcopy(resume or GOOD), letter, RESUME, PROFILE)


def test_clean_resume_passes_and_keeps_an_honest_focus():
    out = check()
    assert out["errors"] == [] and out["dropped_skills"] == []
    assert out["resume"]["title"] == "Senior Data Scientist · Production ML & LLM Systems"


def test_target_job_title_is_replaced_by_the_real_one():
    out = check({**GOOD, "title": "Forward Deployed AI Engineer", "focus": ""})
    assert out["resume"]["title"] == "Senior Data Scientist" and out["title_replaced"] == "Forward Deployed AI Engineer"


def test_focus_that_is_a_title_or_new_tool_is_dropped():
    assert check({**GOOD, "focus": "AI Engineer"})["resume"]["title"] == "Senior Data Scientist"
    assert check({**GOOD, "focus": "Kubernetes & AWS"})["resume"]["title"] == "Senior Data Scientist"


def test_summary_claiming_a_title_is_sent_back():
    out = check({**GOOD, "summary": "Forward deployed AI engineer with 7 years of experience."})
    assert any("forward deployed ai engineer" in e for e in out["errors"])
    # Talking about other people's titles is fine
    assert check({**GOOD, "summary": "Senior data scientist who partners with engineers."})["errors"] == []


def test_skills_not_in_the_resume_are_removed():
    skills = {"Languages": "Python, SQL, JavaScript", "DevOps": "Docker, AWS, CI/CD",
              "Databases": "PostgreSQL, MongoDB", "Cloud": "Google Cloud (Vertex AI, BigQuery, Kubernetes)"}
    out = check({**GOOD, "skills": skills})
    assert out["resume"]["skills"] == {"Languages": "Python, SQL", "Databases": "PostgreSQL",
                                       "Cloud": "Google Cloud (Vertex AI, BigQuery)"}
    assert set(out["dropped_skills"]) == {"JavaScript", "Docker", "AWS", "CI/CD", "MongoDB", "Kubernetes"}


def test_template_example_skills_are_never_treated_as_real():
    assert "JavaScript" not in facts.profile_skills(PROFILE) and "XGBoost" in facts.profile_skills(PROFILE)
    assert "Docker" in facts.template_skills_left(PROFILE)


@pytest.mark.parametrize("change, expected", [
    ({"summary": "Senior data scientist who ships models on AWS and Kubernetes."}, "Mentions aws, kubernetes"),
    ({"summary": "Senior data scientist who cut costs by 65%."}, "Numbers not in the resume: 65"),
    ({"experience": [{"header": "Lead ML Engineer at Woolworths Group", "bullets": []}]},
     "job title 'Lead ML Engineer'"),
    ({"experience": [{"header": "Senior Data Scientist at Google", "bullets": []}]}, "employer 'Google'"),
    ({"summary": "AWS Certified senior data scientist."}, "certified"),
    ({"education": "University of Sydney | PhD"}, "Education doesn't match"),
])
def test_invented_facts_are_sent_back(change, expected):
    errors = check({**GOOD, **change})["errors"]
    assert any(expected.lower() in e.lower() for e in errors), errors


def test_cover_letter_claims():
    good = ("Dear Hiring Manager,\n\nAs a Senior Data Scientist at Woolworths I rebuilt our recommendation "
            "pipelines, cutting running costs by 50%. Your engineers would get the same.\n\nGrant")
    assert check(letter=good)["letter_errors"] == []
    bad = good.replace("As a Senior Data Scientist", "As a machine learning engineer").replace("50%", "60%")
    bad += " I also run Kubernetes."
    problems = " ".join(check(letter=bad)["letter_errors"])
    assert "machine learning engineer" in problems and "60" in problems and "kubernetes" in problems

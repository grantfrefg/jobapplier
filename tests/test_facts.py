"""Fact checks: a tailored resume or letter claims nothing that isn't in the real resume."""

import copy

import pytest

from applypilot.scoring import facts, tailor

RESUME = """Alex Morgan
Profile
Hands-on technical leader with 7 years at Harbourline Retail Group, taking enterprise ML systems from design to
production at national scale. Builds on Google Cloud (Vertex AI, BigQuery). Master of Data Science.
Experience
Harbourline Retail Group (HarbourlineX)
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
Southern Cross Institute of Technology, Master of Data Science
"""
PROFILE = {
    "personal": {"full_name": "Alex Morgan"},
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
    "experience": [{"header": "Senior Data Scientist at Harbourline Retail Group (HarbourlineX)",
                    "subtitle": "Jan 2021 - Present | Python, Vertex AI",
                    "bullets": ["Rebuilt the recommendation pipelines, cutting running costs by 50%"]}],
    "projects": [],
    "education": "Southern Cross Institute of Technology | Master of Data Science",
}


def check(resume=None, letter="", profile=None):
    return tailor.check_facts(copy.deepcopy(resume or GOOD), letter, RESUME, profile or PROFILE)


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
    ({"experience": [{"header": "Lead ML Engineer at Harbourline Retail Group", "bullets": []}]},
     "job title 'Lead ML Engineer'"),
    ({"experience": [{"header": "Senior Data Scientist at Google", "bullets": []}]}, "employer 'Google'"),
    ({"summary": "AWS Certified senior data scientist."}, "certified"),
    ({"education": "University of Sydney | PhD"}, "Education doesn't match"),
])
def test_invented_facts_are_sent_back(change, expected):
    errors = check({**GOOD, **change})["errors"]
    assert any(expected.lower() in e.lower() for e in errors), errors


def test_cover_letter_claims():
    good = ("Dear Hiring Manager,\n\nAs a Senior Data Scientist at Harbourline I rebuilt our recommendation "
            "pipelines, cutting running costs by 50%. Your engineers would get the same.\n\nGrant")
    assert check(letter=good)["letter_errors"] == []
    bad = good.replace("As a Senior Data Scientist", "As a machine learning engineer").replace("50%", "60%")
    bad += " I also run Kubernetes."
    problems = " ".join(check(letter=bad)["letter_errors"])
    assert "machine learning engineer" in problems and "60" in problems and "kubernetes" in problems


def test_rewordings_of_real_skills_are_kept():
    resume = ("Built recommender systems on Google Cloud (Vertex AI) with machine learning and LLMs. "
              "Partner with stakeholders. Personalisation at scale.")
    source = facts.Source(resume)
    skills = {"Skills": "Google Cloud Platform (GCP), Machine learning (ML), Large language models (LLMs), "
                        "Recommendation systems, Stakeholder management, Personalisation, AWS, MLOps, "
                        "Communication skills"}
    kept, dropped = facts.filter_skills(skills, source)
    assert kept["Skills"] == ("Google Cloud Platform (GCP), Machine learning (ML), Large language models (LLMs), "
                              "Recommendation systems, Stakeholder management, Personalisation")
    assert dropped == ["AWS", "MLOps", "Communication skills"]  # never in the resume
    assert source.covers("personalization")  # US and Australian spellings are the same word


def test_real_tools_in_brackets_are_kept_when_the_name_in_front_is_not():
    source = facts.Source("Pipelines on Vertex AI and BigQuery.")
    kept, dropped = facts.filter_skills({"Cloud": "Amazon SageMaker Suite (Vertex AI, BigQuery)"}, source)
    assert kept == {"Cloud": "Vertex AI, BigQuery"} and dropped == ["Amazon SageMaker Suite"]


def test_confirmed_skills_list_counts_template_examples_as_real():
    profile = {"skills_boundary": {"tools": ["Git", "CI/CD"], "ml": ["MLOps"]}}
    assert facts.profile_skills(profile) == ["MLOps"] and facts.template_skills_left(profile) == ["Git", "CI/CD"]
    confirmed = {**profile, "skills_confirmed": True}
    assert facts.profile_skills(confirmed) == ["Git", "CI/CD", "MLOps"] and facts.template_skills_left(confirmed) == []


@pytest.mark.parametrize("text, claims", [
    ("Experienced engineer building cloud services.", []),
    ("A pragmatic, results-driven engineer who ships.", []),
    ("Machine learning engineer with four years in production.", ["machine learning engineer"]),
])
def test_describing_words_are_not_job_titles(text, claims):
    source = facts.Source("Software Engineer at Acme\n- Built backend services in Python", [])
    assert facts.role_claims(text, source) == claims


@pytest.mark.parametrize("line, header_employer", [
    ("Acme Corp    Jan 2020 - Present", "Acme Corp"),
    ("Acme Corp\tSydney NSW", "Acme Corp"),
    ("Software Engineer - Acme Corp", "Acme Corp"),
    ("Software Engineer – Acme Corp (2019-2023)", "Acme Corp"),
    ("Software Engineer | Acme Pty Ltd | 2021 - Present", "Acme"),  # the AI dropped "Pty Ltd"
    ("Acme, Melbourne", "Acme Pty Ltd"),  # or added it
    ("ACME CORP Sydney", "Acme Corp"),
    ("Software Engineer @ Smith & Jones", "Smith and Jones"),
    ("Analyst at Westpac Group, Feb 2018 – Mar 2020", "Westpac"),
])
def test_employers_laid_out_the_usual_ways_are_real(line, header_employer):
    source = facts.Source(f"Jane Doe\nExperience\n{line}\n- Built things\n")
    assert source.names_employer(header_employer)


def test_part_of_a_longer_name_is_not_an_employer():
    source = facts.Source("Builds on Google Cloud (Vertex AI).\nAcme Corp, Sydney\n")
    assert not source.names_employer("Google") and not source.names_employer("Acme Bank")


def test_profile_only_skills_stay_in_the_skills_list():
    profile = {**PROFILE, "skills_confirmed": True,
               "skills_boundary": {"tools": ["Kubernetes", "Python", "Stakeholder management", "Leadership"]}}
    # Python is in the resume, and soft skills aren't tied to a role
    assert facts.profile_only_skills(RESUME, profile) == ["Kubernetes"]
    role = {"header": "Senior Data Scientist at Harbourline Retail Group",
            "subtitle": "Jan 2021 - Present | Python, Kubernetes, PyTorch",
            "bullets": ["Technical lead for production recommender systems reaching 10 million customers."]}
    out = check({**GOOD, "skills": {"Tools": "Python, Kubernetes"}, "experience": [role]}, profile=profile)
    assert out["resume"]["experience"][0]["subtitle"] == "Jan 2021 - Present | Python, PyTorch"
    assert "Kubernetes" in out["resume"]["skills"]["Tools"]  # still in the skills list
    assert not any("Kubernetes" in e for e in out["errors"])
    role["bullets"] = ["Deployed recommender systems on Kubernetes for 10 million customers."]
    errors = check({**GOOD, "experience": [role]}, profile=profile)["errors"]
    assert any("names Kubernetes" in e and "SKILLS only" in e for e in errors), errors

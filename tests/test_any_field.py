"""Nothing assumes the candidate is a software engineer (reported by a marketer using the app)."""

import json

import pytest

from applypilot import config, pipeline, profile_options as po
from applypilot.scoring import facts, pdf, tailor
from applypilot.ui import server

ENGINEERING = ("python", "fastapi", "flask", "react", "docker", "devops", "frameworks", "software engineer",
               "github.com/yourusername")


# -- searches ------------------------------------------------------------------

def test_no_saved_search_means_no_search(tmp_path, monkeypatch):
    """It used to fall back to the bundled example: software-engineering jobs in San Francisco."""
    monkeypatch.setattr(config, "SEARCH_CONFIG_PATH", tmp_path / "searches.yaml")
    assert config.load_search_config() == {}
    (tmp_path / "searches.yaml").write_text("")
    assert config.load_search_config() == {}


def test_discover_asks_for_a_search_first(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "SEARCH_CONFIG_PATH", tmp_path / "searches.yaml")
    import applypilot.discovery.jobspy as js
    monkeypatch.setattr(js, "run_discovery", lambda *a, **k: pytest.fail("searched with no saved search"))
    stats = pipeline._run_discover()
    assert all(v.startswith("skipped") for v in stats.values())
    assert "What jobs to look for" in capsys.readouterr().out


# -- the profile -----------------------------------------------------------------

def _template() -> dict:
    return json.loads(server.PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_profile_template_is_for_any_field():
    text = json.dumps(_template()).lower()
    assert not any(word in text for word in ENGINEERING), [w for w in ENGINEERING if w in text]
    assert set(_template()["skills_boundary"]) == {"skills", "tools_and_software", "certifications_and_licences"}
    assert _template()["experience"]["target_role"] == ""


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    for name, value in {"APP_DIR": tmp_path, "ENV_PATH": tmp_path / ".env", "PROFILE_PATH": tmp_path / "profile.json",
                        "SEARCH_CONFIG_PATH": tmp_path / "searches.yaml", "DB_PATH": tmp_path / "t.db",
                        "LOG_DIR": tmp_path / "logs"}.items():
        monkeypatch.setattr(config, name, value)
    from applypilot import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "load_env", lambda: None)
    with TestClient(server.create_app(), base_url="http://localhost") as c:
        yield c
    database.close_connection(tmp_path / "t.db")


def test_setup_shows_field_neutral_profile_and_search(client):
    data = client.get("/api/profile").json()
    assert set(data["profile"]["skills_boundary"]) == {"skills", "tools_and_software", "certifications_and_licences"}
    searches = client.get("/api/searches").json()
    assert searches["exists"] is False and "engineer" not in searches["yaml"].lower()


def test_a_saved_profile_keeps_its_own_skill_categories(client):
    """Older profiles have the engineering categories: they stay as saved, without empty new ones beside them."""
    saved = {"personal": {"full_name": "Sam"}, "skills_boundary": {"channels": ["Google Ads"], "tools": ["HubSpot"]}}
    config.PROFILE_PATH.write_text(json.dumps(saved))
    assert client.get("/api/profile").json()["profile"]["skills_boundary"] == saved["skills_boundary"]


def test_the_old_templates_examples_are_still_examples():
    """A profile started from the old template may still hold its Python/FastAPI examples."""
    profile = {"skills_boundary": {"languages": ["Python", "SQL"], "tools": ["HubSpot"]}}
    assert facts.profile_skills(profile) == ["HubSpot"]
    assert facts.profile_skills({**profile, "skills_confirmed": True}) == ["Python", "SQL", "HubSpot"]
    assert po.placeholder_fields({"personal": {"github_url": "https://github.com/yourusername"}}) == \
        ["personal.github_url"]


# -- tailored resumes --------------------------------------------------------------

def test_resumes_say_skills_not_technical_skills():
    data = {"title": "Performance Marketing Manager", "summary": "Marketer.",
            "skills": {"Channels": "Google Ads, LinkedIn Ads", "Analytics": "GA4"},
            "experience": [], "education": "UTS"}
    text = tailor.assemble_resume_text(data, {"personal": {"full_name": "Sam Taylor"}})
    assert "\nSKILLS\n" in text and "TECHNICAL SKILLS" not in text
    html = pdf.build_html(pdf.parse_resume(text))
    assert "Google Ads, LinkedIn Ads" in html
    old = text.replace("\nSKILLS\n", "\nTECHNICAL SKILLS\n")  # resumes made before still render
    assert "Google Ads, LinkedIn Ads" in pdf.build_html(pdf.parse_resume(old))


# -- industries ----------------------------------------------------------------------

MARKETER = {"experience": {"industry": "Marketing and communications", "target_role": "Growth Marketing Lead"}}


def test_industries_are_well_formed():
    from applypilot import industries
    assert "Marketing and communications" in industries.NAMES and industries.NAMES[-1] == "Other"
    for name, d in industries.INDUSTRIES.items():
        keys = [k for k, _ in d["skills"]]
        assert len(keys) == len(set(keys)) and all(k == k.lower() and " " not in k for k in keys), name
        assert all(examples for _, examples in d["skills"]) and 3 <= len(d["resume"]) <= 6, name
    other = [k for k, _ in industries.INDUSTRIES["Other"]["skills"]]
    assert other == list(_template()["skills_boundary"])  # a new profile starts with Other's boxes
    assert industries.of({"experience": {"industry": "Basket weaving"}}) == ""  # unknown: as if not set


def test_industry_is_a_profile_choice(client):
    data = client.get("/api/profile").json()
    values = [o["value"] for o in data["options"]["experience.industry"]]
    assert values[0] == "" and "Marketing and communications" in values
    assert data["skill_layouts"]["Marketing and communications"][0][0] == "channels_and_specialisms"
    assert po.invalid_choices({"experience": {"industry": "Basket weaving"}}) == ["experience.industry"]


def test_tailoring_groups_skills_the_industrys_way():
    rules = tailor._tailor_rules(MARKETER)
    assert "works in marketing and communications: group their skills like Channels, Platforms & Tools" in rules
    shape = json.loads(tailor._resume_shape(MARKETER).replace('"..."', '""'))
    assert list(shape["skills"]) == ["Channels", "Platforms & Tools", "Analytics", "Content & Creative"]
    assert "works in" not in tailor._tailor_rules({})  # no industry: as before


def test_the_scorer_is_told_the_field():
    from applypilot.scoring.context import candidate_brief
    assert "Target role: Growth Marketing Lead (field: marketing and communications)" in candidate_brief(MARKETER)


# -- 0.14.1: the rest of the review ----------------------------------------------------

def test_form_filling_never_calls_the_applicant_an_engineer():
    from applypilot import market
    from applypilot.apply import local_engine as le
    au = le._system_prompt()
    assert "engineer" not in au.lower() and "in whatever field the profile and resume show" in au
    uk = market.localise(le._SYSTEM_PROMPT, le._UK_SWAPS, market.UK)
    assert "engineer" not in uk.lower() and "applying in the UK" in uk


def test_agent_engines_describe_the_real_candidate():
    from applypilot.apply import prompt
    base = {"personal": {"city": "Parramatta"}, "work_authorization": {}}

    def line(exp):
        text = prompt._build_screening_section({**base, "experience": exp})
        return next(row for row in text.splitlines() if "This candidate" in row)
    assert "software engineer" not in line({}) and " a  with" not in line({"target_role": ""})
    assert "a Marketing Coordinator in marketing and communications with 2 years" in line(
        {"current_job_title": "Marketing Coordinator", "industry": "Marketing and communications",
         "years_of_experience_total": "2"})


def test_a_fields_set_phrases_are_not_banned_words():
    from applypilot.scoring import cover_letter
    from applypilot.scoring.validator import banned_in, validate_cover_letter
    assert banned_in("Mapped the customer journey, analysed the competitive landscape, ran dynamic creative "
                     "and grew the partner ecosystem.") == []
    assert banned_in("A dynamic marketer on a journey.") == ["dynamic", "journey"]  # alone they still are
    letter = ("Dear Hiring Manager,\n\nAt Cloudline I mapped the customer journey for our paid search and cut "
              "cost per lead 32%.\n\nKind regards,\nSam")
    assert not any("Banned" in e for e in validate_cover_letter(letter)["errors"])
    assert "set phrases are fine" in tailor._tailor_rules({}) and "set phrases are fine" in \
        cover_letter.cover_letter_rules({})


@pytest.mark.parametrize("text, claim", [
    ("Senior growth marketer with 7 years", "growth marketer"),
    ("Results-driven demand generation lead", "demand generation lead"),
    ("A chartered accountant with 10 years", "chartered accountant"),
    ("Registered nurse", "registered nurse"),
    ("Most recently head of growth at Cloudline", "head of growth"),
])
def test_invented_titles_in_any_field_are_caught(text, claim):
    source = facts.Source("SAM TAYLOR\nPerformance Marketing Manager, Cloudline\n- Built HubSpot lead scoring.")
    assert facts.role_claims(text, source) == [claim]


@pytest.mark.parametrize("text", [
    "Generated 300 marketing qualified leads a month", "Owns lead generation and lead scoring",
    "Presented results to the executive team", "Built buyer personas", "Wrote the executive summary",
    "Worked with the sales team and account executives", "Performance marketing manager with 5 years",
])
def test_ordinary_marketing_phrases_are_not_titles(text):
    source = facts.Source("SAM TAYLOR\nPerformance Marketing Manager, Cloudline\n- Built HubSpot lead scoring.")
    assert facts.role_claims(text, source) == []


def test_ats_keywords_and_tools_cover_other_fields():
    from applypilot.scoring import ats

    def terms(ad):
        return ats.ad_terms({"title": "x", "full_description": ad})
    assert {"seo", "copywriting", "email marketing", "hubspot"} <= set(terms(
        "Content Marketing Manager: SEO, copywriting, email marketing, HubSpot."))
    assert {"xero", "reconciliations", "bas", "payroll"} <= set(terms(
        "Accountant: reconciliations, BAS, payroll, Xero."))
    assert {"wound care", "medication administration"} <= set(terms("Nurse: wound care, medication administration."))
    assert facts.new_tools("Ran month-end in Xero and NetSuite", facts.Source("Ran Google Ads.")) == \
        ["netsuite", "xero"]


def test_tailoring_wording_is_not_an_engineers():
    rules = tailor._tailor_rules({})
    assert "Shipped" not in rules and "main tech" not in rules and "what was built" not in rules


def test_the_terminal_wizard_asks_about_any_field(tmp_path, monkeypatch):
    from applypilot.wizard import init
    asked: list[str] = []
    answers = {"Full name": "Sam Taylor", "Email address": "sam@example.com", "City": "Parramatta",
               "Country": "Australia", "Current/most recent job title": "Marketing Coordinator",
               "Industry": "2", "Channels and specialisms": "paid social, email"}

    def ask(question, default="", **kw):
        asked.append(question)
        return next((v for k, v in answers.items() if question.startswith(k)), default or "")
    monkeypatch.setattr(init.Prompt, "ask", staticmethod(ask))
    monkeypatch.setattr(init.Confirm, "ask", staticmethod(lambda *a, **k: k.get("default", True)))
    monkeypatch.setattr(init, "PROFILE_PATH", tmp_path / "profile.json")
    profile = init._setup_profile()
    text = " ".join(asked).lower()
    assert "programming languages" not in text and "frameworks" not in text and "backend engineer" not in text
    assert profile["experience"]["industry"] == "Marketing and communications"
    assert profile["experience"]["current_job_title"] == "Marketing Coordinator"
    assert profile["skills_boundary"]["channels_and_specialisms"] == ["paid social", "email"]


def test_the_preview_sample_follows_the_industry():
    from applypilot.scoring import look
    marketer = {"experience": {"industry": "Marketing and communications"}}
    assert "Performance Marketing Manager" in look.sample_for(marketer)
    assert "Operations Coordinator" in look.sample_for({"experience": {"industry": "Finance and accounting"}})
    assert look.sample_for({}) == look.SAMPLE  # nothing set: as before


# -- step 4: "Other: type your field", or the AI judges the field from the resume ----------

def test_the_field_comes_from_the_industry_or_what_you_typed():
    from applypilot import industries
    f = industries.field
    assert f({"experience": {"industry": "Healthcare", "field": "ignored"}}) == "Healthcare"
    assert f({"experience": {"industry": "Other", "field": " Business analysis \n"}}) == "Business analysis"
    assert f({"experience": {"field": "Nursing"}}) == "Nursing"
    assert f({"experience": {"industry": "Other"}}) == "" and f({}) == ""
    assert len(f({"experience": {"field": "x" * 200}})) == 60


def test_every_prompt_uses_the_field_or_says_to_judge_it():
    from applypilot.apply import prompt
    from applypilot.scoring.context import candidate_brief
    ba = {"experience": {"industry": "Other", "field": "Business analysis", "current_job_title": "Business Analyst"}}
    assert "(field: business analysis)" in candidate_brief(ba)
    assert "This candidate works in business analysis: group their skills the way people in that field do" in \
        tailor._tailor_rules(ba)
    assert "a Business Analyst in business analysis" in prompt._build_screening_section(
        {**ba, "personal": {}, "work_authorization": {}})
    assert "(field not stated: judge it from the resume)" in candidate_brief({})
    assert "Work out the candidate's field from their resume" in tailor._tailor_rules({})
    # A named industry keeps its own skill groups
    assert "group their skills like Channels" in tailor._tailor_rules(MARKETER)


def test_setup_offers_the_field_box(client):
    profile = client.get("/api/profile").json()["profile"]
    keys = list(profile["experience"])
    assert keys.index("field") == keys.index("industry") + 1 and profile["experience"]["field"] == ""

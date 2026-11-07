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

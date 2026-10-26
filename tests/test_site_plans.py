"""Smart extract remembers how to read each site (improvement C)."""

import pytest

from applypilot import config
from applypilot.discovery import smartextract as se

JSON_LD_PLAN = ('{"strategy": "json_ld", "reasoning": "JobPosting entries", "extraction": '
                '{"title": "title", "company": "hiringOrganization.name", "location": '
                '"jobLocation.address.addressLocality", "url": "url", "salary": null, "description": null}}')


def _intel(titles, html=""):
    return {"json_ld": [{"@type": "JobPosting", "title": t, "url": f"https://jora.test/{i}",
                         "hiringOrganization": {"name": "Acme"},
                         "jobLocation": {"address": {"addressLocality": "Sydney"}}}
                        for i, t in enumerate(titles)],
            "api_responses": [], "data_testids": [], "card_candidates": [], "full_html": html}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    pages = []
    calls = []
    monkeypatch.setattr(se, "collect_page_intelligence", lambda url, headless=True: pages.pop(0))
    monkeypatch.setattr(se, "format_strategy_briefing", lambda intel: "briefing")

    def fake_llm(prompt):
        calls.append(prompt)
        return calls_replies.pop(0), 0.1, {"response_chars": 10}

    calls_replies: list[str] = []
    monkeypatch.setattr(se, "ask_llm", fake_llm)
    return pages, calls, calls_replies


def test_learned_plan_is_reused_without_ai(site, tmp_path):
    pages, calls, replies = site
    pages += [_intel(["Data Analyst", "BI Analyst"]), _intel(["Data Engineer"])]
    replies.append(JSON_LD_PLAN)

    first = se._run_one_site("Jora", "https://jora.test/?q=analyst")
    assert first["status"] == "PASS" and first["plan_source"] == "ai" and len(calls) == 1
    assert (tmp_path / "site_plans" / "jora.json").exists()

    # A different query on the same site: the saved plan, no AI request
    second = se._run_one_site("Jora", "https://jora.test/?q=engineer")
    assert second["plan_source"] == "saved" and len(calls) == 1
    assert [j["title"] for j in second["jobs"]] == ["Data Engineer"]
    assert second["jobs"][0]["company"] == "Acme"


def test_plan_that_stops_working_is_relearned(site):
    pages, calls, replies = site
    se.save_site_plan("Jora", {"strategy": "json_ld", "extraction": {"title": "name"}})  # outdated
    pages.append(_intel(["Data Analyst"]))
    replies.append(JSON_LD_PLAN)
    r = se._run_one_site("Jora", "https://jora.test/?q=analyst")
    assert r["plan_source"] == "ai" and len(calls) == 1 and r["titles"] == 1
    assert se.load_site_plan("Jora")["extraction"]["title"] == "title"  # replaced with the working one


def test_failed_relearn_keeps_saved_plan(site):
    pages, calls, replies = site
    se.save_site_plan("Jora", {"strategy": "json_ld", "extraction": {"title": "title"}})
    pages.append(_intel([]))  # e.g. a query with no results
    replies.append(JSON_LD_PLAN)
    r = se._run_one_site("Jora", "https://jora.test/?q=rare")
    assert r["status"] == "FAIL"
    assert se.load_site_plan("Jora")["extraction"] == {"title": "title"}


def test_blocked_page_keeps_plan_and_skips_ai(site):
    pages, calls, _ = site
    se.save_site_plan("Jora", {"strategy": "json_ld", "extraction": {"title": "title"}})
    pages.append(_intel([], html="<p>Please verify you are a human (captcha)</p>"))
    r = se._run_one_site("Jora", "https://jora.test/?q=analyst")
    assert r["status"] == "FAIL" and calls == []
    assert se.load_site_plan("Jora") is not None


def test_saved_css_selectors_run_without_ai(site):
    pages, calls, _ = site
    se.save_site_plan("Board", {"strategy": "css_selectors", "extraction": {
        "job_card": "div.job", "title": "h2", "company": ".co", "url": "a", "location": None}})
    html = ('<div class="job"><h2>Analyst</h2><span class="co">Acme</span><a href="/j/1">x</a></div>'
            '<div class="job"><h2>Engineer</h2><span class="co">Beta</span><a href="/j/2">x</a></div>')
    pages.append({**_intel([]), "full_html": html + "<p>Filler listing text.</p>" * 600})
    r = se._run_one_site("Board", "https://board.test/")
    assert calls == [] and r["plan_source"] == "saved"
    assert [(j["title"], j["company"], j["url"]) for j in r["jobs"]] == [
        ("Analyst", "Acme", "/j/1"), ("Engineer", "Beta", "/j/2")]


def test_corrupt_plan_file_is_ignored(site, tmp_path):
    (tmp_path / "site_plans").mkdir()
    (tmp_path / "site_plans" / "jora.json").write_text("{not json")
    assert se.load_site_plan("Jora") is None

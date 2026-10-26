"""Discovery behaviour for an Australian search config."""

import sqlite3

import yaml

from applypilot import config
from applypilot.discovery import jobspy as js, smartextract as se, workday as wd
from applypilot.wizard.init import build_search_yaml

AU_CFG = yaml.safe_load(build_search_yaml("Sydney, NSW", ["Data Analyst"], "australia", True))


def test_generated_config_targets_australia():
    assert AU_CFG["country"] == "australia"
    assert AU_CFG["sites"] == ["indeed", "linkedin"]
    assert AU_CFG["smart_extract_sites"] == ["Jora"]
    assert AU_CFG["workday_employers"] == []
    assert AU_CFG["location_accept"] == ["Sydney", "NSW", "New South Wales"]
    assert {"location": "Australia", "remote": True} in AU_CFG["locations"]
    assert js.country_indeed(AU_CFG) == "australia"


def test_other_countries_keep_every_source():
    cfg = yaml.safe_load(build_search_yaml("Toronto, ON", ["Dev"], "canada"))
    assert "smart_extract_sites" not in cfg and "workday_employers" not in cfg
    assert js.country_indeed(cfg) == "canada"
    assert js.country_indeed({}) == "usa"  # upstream default unchanged


def test_location_filter():
    accept, reject = config.load_location_filter(AU_CFG)
    ok = lambda loc: config.location_ok(loc, accept, reject)  # noqa: E731
    assert ok("Sydney, NSW, Australia")
    assert ok("Greater Sydney Area")
    assert ok("Parramatta, New South Wales, Australia")
    assert ok("Remote") and ok(None)
    assert not ok("Melbourne, VIC")
    assert not ok("Toronto, ON, Canada")
    # Whole-word matching: "NSW" must not match inside other words
    assert not config.location_ok("Answer Bay", ["NSW"], [])


def test_empty_accept_list_keeps_everything_not_rejected():
    """Regression: an empty accept list used to drop every non-remote job."""
    assert config.location_ok("Sydney, NSW", [], [])
    assert not config.location_ok("Sydney, NSW", [], ["Sydney"])


def test_accept_patterns_expand_states():
    assert config.location_accept_patterns("Melbourne, VIC") == ["Melbourne", "VIC", "Victoria"]
    assert config.location_accept_patterns("Remote") == []


def test_smart_extract_allowlist(monkeypatch):
    monkeypatch.setattr(config, "load_search_config", lambda: AU_CFG)
    seen = {}
    monkeypatch.setattr(se, "_run_all", lambda targets, *a, **k: seen.setdefault("targets", targets) and {})
    se.run_smart_extract()
    names = {t["name"] for t in seen["targets"]}
    assert names == {"Jora"}
    assert seen["targets"][0]["url"] == "https://au.jora.com/j?sp=search&q=Data+Analyst&l=Sydney%2C+NSW"


def _capture_workday(monkeypatch, cfg):
    monkeypatch.setattr(config, "load_search_config", lambda: cfg)
    crawled = []

    def fake_scrape(search_text, employers, **kw):
        crawled.append(sorted(employers))
        return {"new": 0, "existing": 0, "found": 0}

    monkeypatch.setattr(wd, "scrape_employers", fake_scrape)
    return crawled


def test_workday_allowlist_empty_means_off(monkeypatch):
    crawled = _capture_workday(monkeypatch, AU_CFG)
    assert wd.run_workday_discovery()["queries"] == 0
    assert crawled == []


def test_workday_allowlist_by_key_or_name(monkeypatch):
    crawled = _capture_workday(monkeypatch, {**AU_CFG, "workday_employers": ["TD Bank", "cibc"]})
    wd.run_workday_discovery()
    assert crawled == [["cibc", "td"]]


def test_workday_without_allowlist_crawls_everything(monkeypatch):
    cfg = {k: v for k, v in AU_CFG.items() if k != "workday_employers"}
    crawled = _capture_workday(monkeypatch, cfg)
    wd.run_workday_discovery()
    assert crawled and len(crawled[0]) == len(wd.load_employers())


def test_relative_jora_urls_resolved(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title, salary, description, location, site, "
                 "strategy, discovered_at)")
    jobs = [{"url": "/job/Data-Analyst-abc?x=1", "title": "Data Analyst", "location": "Sydney NSW"},
            {"url": "/job/Other-def", "title": "Other", "location": "Perth WA"}]
    new, _ = se._store_jobs_filtered(conn, jobs, "Jora", "css", *config.load_location_filter(AU_CFG))
    rows = [r[0] for r in conn.execute("SELECT url FROM jobs")]
    assert new == 1 and rows == ["https://au.jora.com/job/Data-Analyst-abc?x=1"]

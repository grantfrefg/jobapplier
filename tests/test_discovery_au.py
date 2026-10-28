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
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title, company, salary, description, location, "
                 "site, strategy, discovered_at)")
    jobs = [{"url": "/job/Data-Analyst-abc?x=1", "title": "Data Analyst", "location": "Sydney NSW"},
            {"url": "/job/Other-def", "title": "Other", "location": "Perth WA"}]
    new, _ = se._store_jobs_filtered(conn, jobs, "Jora", "css", *config.load_location_filter(AU_CFG))
    rows = [r[0] for r in conn.execute("SELECT url FROM jobs")]
    assert new == 1 and rows == ["https://au.jora.com/job/Data-Analyst-abc?x=1"]


def test_blocked_board_is_named_and_skipped_for_the_rest_of_the_run(monkeypatch, caplog):
    """A 429 from LinkedIn used to print as an unnamed 'response status code 429 (blocked: use a proxy)'
    on every search; now it's named once and LinkedIn isn't asked again this run."""
    import logging

    import pandas as pd

    calls = []

    def fake_scrape(site_name, search_term, **kwargs):
        calls.append(list(site_name))
        if "linkedin" in site_name:  # JobSpy logs the failure under jobspy.linkedin and carries on
            logging.getLogger("jobspy.linkedin").error("response status code 429 (blocked: use a proxy)")
        return pd.DataFrame([{"job_url": f"https://au.indeed.com/viewjob?jk={search_term}", "title": search_term,
                              "company": "Acme", "location": "Sydney, NSW", "site": "indeed"}])

    monkeypatch.setattr(js, "scrape_jobs", fake_scrape)
    cfg = {"queries": [{"query": "Data Scientist"}, {"query": "ML Engineer"}, {"query": "Data Engineer"}],
           "locations": [{"location": "Sydney, NSW", "label": "Sydney"}]}
    with caplog.at_level(logging.INFO):
        result = js._full_crawl(cfg, sites=["indeed", "linkedin"], max_retries=0)
    assert calls == [["indeed", "linkedin"], ["indeed"], ["indeed"]]
    assert result["blocked"] == {"linkedin": "blocked: too many requests (HTTP 429)"}
    assert result["new"] == 3
    text = caplog.text
    assert text.count("LinkedIn: blocked: too many requests (HTTP 429). Skipping LinkedIn") == 1
    assert "LinkedIn was skipped this run" in text and "few hours" in text
    assert logging.getLogger("jobspy").propagate  # restored afterwards


def test_other_board_errors_are_named_but_not_skipped(monkeypatch, caplog):
    import logging

    import pandas as pd

    calls = []

    def fake_scrape(site_name, search_term, **kwargs):
        calls.append(list(site_name))
        logging.getLogger("jobspy.indeed").error("API error: something odd")
        return pd.DataFrame()

    monkeypatch.setattr(js, "scrape_jobs", fake_scrape)
    cfg = {"queries": [{"query": "A"}, {"query": "B"}], "locations": [{"location": "Sydney, NSW"}]}
    with caplog.at_level(logging.INFO):
        result = js._full_crawl(cfg, sites=["indeed"], max_retries=0)
    assert calls == [["indeed"], ["indeed"]] and result["blocked"] == {}
    assert caplog.text.count("Indeed: API error: something odd") == 2


def test_failed_searches_are_recorded_and_retried_alone(monkeypatch):
    """A board that blocks or errors leaves its searches for `rerun discover`; one-job warnings don't count."""
    import logging

    import pandas as pd

    from applypilot.discovery import retry

    calls = []
    linkedin_blocked = [True]

    def fake_scrape(site_name, search_term, **kwargs):
        calls.append((search_term, list(site_name)))
        if "linkedin" in site_name and linkedin_blocked[0]:
            logging.getLogger("jobspy.linkedin").error("response status code 429 (blocked: use a proxy)")
        logging.getLogger("jobspy.indeed").warning("skipping job: odd card")  # one job, not the search
        return pd.DataFrame()

    monkeypatch.setattr(js, "scrape_jobs", fake_scrape)
    cfg = {"queries": [{"query": "A"}, {"query": "B"}], "locations": [{"location": "Sydney, NSW"}]}
    result = js._full_crawl(cfg, sites=["indeed", "linkedin"], max_retries=0)
    assert result["failed"] == 2  # A: LinkedIn blocked; B: LinkedIn skipped after that
    assert [(f["query"], f["sites"]) for f in retry.load("jobspy")] == [("A", ["linkedin"]), ("B", ["linkedin"])]
    assert retry.summary() == {"searches": 2, "boards": ["linkedin"]}

    # The retry asks only LinkedIn, only for those searches, and clears the list when they work
    calls.clear()
    linkedin_blocked[0] = False
    monkeypatch.setattr(config, "load_search_config", lambda: cfg)
    js.run_discovery(retry_failed=True)
    assert calls == [("A", ["linkedin"]), ("B", ["linkedin"])]
    assert retry.load("jobspy") == [] and not (config.APP_DIR / "discover_failed.json").exists()

    calls.clear()
    assert js.run_discovery(retry_failed=True)["queries"] == 0 and calls == []  # nothing left to retry

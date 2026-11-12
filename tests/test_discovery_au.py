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
    assert AU_CFG["smart_extract_sites"] == ["Jora", "Workforce Australia", "Randstad Australia", "Hays Australia"]
    assert "workday_employers" not in AU_CFG  # Workday: the employers tagged for Australia
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
    urls = {t["name"]: t["url"] for t in seen["targets"]}
    assert set(urls) == {"Jora", "Workforce Australia", "Randstad Australia", "Hays Australia"}
    assert urls["Jora"] == "https://au.jora.com/j?sp=search&q=Data+Analyst&l=Sydney%2C+NSW"
    assert urls["Randstad Australia"] == "https://www.randstad.com.au/jobs/q-data-analyst/"  # query in the path
    assert urls["Hays Australia"].endswith("q=Data+Analyst&location=Sydney")  # the city alone
    assert "searchText=Data+Analyst&locationName=Sydney" in urls["Workforce Australia"]


def _capture_workday(monkeypatch, cfg):
    monkeypatch.setattr(config, "load_search_config", lambda: cfg)
    crawled = []

    def fake_scrape(search_text, employers, **kw):
        crawled.append(sorted(employers))
        return {"new": 0, "existing": 0, "found": 0}

    monkeypatch.setattr(wd, "scrape_employers", fake_scrape)
    return crawled


def test_workday_allowlist_empty_means_off(monkeypatch):
    crawled = _capture_workday(monkeypatch, {**AU_CFG, "workday_employers": []})
    assert wd.run_workday_discovery()["queries"] == 0
    assert crawled == []


def test_workday_allowlist_by_key_or_name(monkeypatch):
    crawled = _capture_workday(monkeypatch, {**AU_CFG, "workday_employers": ["TD Bank", "cibc"]})
    wd.run_workday_discovery()
    assert crawled == [["cibc", "td"]]


def test_workday_searches_only_employers_hiring_in_the_country(monkeypatch):
    """It used to crawl every employer, all picked for Canada, whatever the search's country."""
    crawled = _capture_workday(monkeypatch, AU_CFG)
    wd.run_workday_discovery()
    assert crawled and "salesforce" in crawled[0] and "cisco" in crawled[0]
    assert "cibc" not in crawled[0] and "canadian_tire" not in crawled[0]
    assert all("australia" in wd.load_employers()[k]["countries"] for k in crawled[0])
    uk = _capture_workday(monkeypatch, {**AU_CFG, "country": "United Kingdom"})
    wd.run_workday_discovery()
    assert "td" in uk[-1] and "moderna" in uk[-1] and "cae" not in uk[-1]


def test_workday_registry_says_where_each_employer_hires():
    employers = wd.load_employers()
    assert employers and all(e.get("countries") for e in employers.values())
    assert wd.country_key("United Kingdom") == "uk" and wd.country_key(" Australia ") == "australia"
    assert wd.employers_for_country(employers, "") == employers  # no country set: every employer
    assert wd.employers_for_country(employers, "Narnia") == {}


def test_workday_with_no_employers_for_the_country_says_so(monkeypatch, caplog):
    import logging
    crawled = _capture_workday(monkeypatch, {**AU_CFG, "country": "new zealand"})
    with caplog.at_level(logging.INFO):
        assert wd.run_workday_discovery()["queries"] == 0
    assert crawled == [] and "no employers to search for New Zealand" in caplog.text


def test_relative_jora_urls_resolved(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title, company, salary, description, location, "
                 "site, strategy, discovered_at)")
    jobs = [{"url": "/job/Data-Analyst-abc?x=1", "title": "Data Analyst", "location": "Sydney NSW"},
            {"url": "/job/Other-def", "title": "Other", "location": "Perth WA"}]
    new, _ = se._store_jobs_filtered(conn, jobs, "Jora", "css", *config.load_location_filter(AU_CFG))
    rows = [r[0] for r in conn.execute("SELECT url FROM jobs")]
    assert new == 1 and rows == ["https://au.jora.com/job/Data-Analyst-abc?x=1"]



def test_non_web_links_are_not_stored(tmp_path):
    """Job links read from a page by the AI end up as clickable links in the app: only web addresses."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title, company, salary, description, location, "
                 "site, strategy, discovered_at)")
    jobs = [{"url": "javascript:alert(1)", "title": "Bad", "location": "Sydney NSW"},
            {"url": "data:text/html,hi", "title": "Bad 2", "location": "Sydney NSW"},
            {"url": "https://example.com/job/1", "title": "Good", "location": "Sydney NSW"}]
    for site in ("Jora", "Some board without a base URL"):
        conn.execute("DELETE FROM jobs")
        se._store_jobs_filtered(conn, jobs, site, "css", *config.load_location_filter(AU_CFG))
        assert [r[0] for r in conn.execute("SELECT url FROM jobs")] == ["https://example.com/job/1"]

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


def test_workforce_australia_jobs_get_a_link_and_readable_fields(tmp_path, monkeypatch):
    """Its API results have an id but no link, and objects for location and salary: they used to be
    dropped (no link) and stored as "{'code': ...}"."""
    from applypilot import config, database
    from applypilot.discovery import smartextract as se
    item = {"result": {"title": "Marketing Specialist", "employerName": "Acme Pty Ltd", "vacancyId": 2354646055,
                       "location": {"code": "21WSNE", "label": "NSW - Sydney - Western Sydney & Nepean"},
                       "salary": {"code": "SLL4", "label": "$80,001 - $100,000"}}}
    plan = {"strategy": "api_response", "extraction": {
        "url_pattern": "/api/v1/global/vacancies/", "items_path": "results", "title": "result.title",
        "company": "result.employerName", "location": "result.location", "salary": "result.salary"}}
    intel = {"api_responses": [{"url": "https://x/api/v1/global/vacancies/?q=1", "_raw_data": {"results": [item]}}]}
    [job] = se.execute_api_response(intel, plan)
    assert job["location"] == "NSW - Sydney - Western Sydney & Nepean" and job["salary"] == "$80,001 - $100,000"
    assert job["url"] is None

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "jobs.db")
    conn = database.init_db(tmp_path / "jobs.db")
    assert "Workforce Australia" in config.load_job_url_templates()
    new, _ = se._store_jobs_filtered(conn, [job], "Workforce Australia", "api_response", ["Sydney"], [])
    assert new == 1
    url, location = conn.execute("SELECT url, location FROM jobs").fetchone()
    assert url == "https://www.workforceaustralia.gov.au/individuals/jobs/details/2354646055"
    assert location.startswith("NSW - Sydney")
    # No id to fill the template with: still skipped, never a broken link
    assert se._url_from_template("https://x/{result.vacancyId}", {"result": {}}) is None
    database.close_connection(tmp_path / "jobs.db")


def test_workday_skips_jobs_it_already_has(tmp_path, monkeypatch):
    """The same role turns up for several searches: its details are fetched once."""
    from applypilot import database
    from applypilot.discovery import workday
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "jobs.db")
    conn = database.init_db(tmp_path / "jobs.db")
    conn.execute("INSERT INTO jobs (url, title, site) VALUES (?, 'Saved earlier', 'PayPal')",
                 ("https://wd1.myworkdaysite.com/recruiting/paypal/jobs/job/Sydney/Analyst_R1",))
    conn.commit()
    monkeypatch.setattr(workday, "_seen", set())
    jobs = [{"external_path": "/job/Sydney/Analyst_R1"}, {"external_path": "/job/Sydney/Engineer_R2"},
            {"external_path": "/job/Sydney/Engineer_R2"}]
    assert [j["external_path"] for j in workday._not_seen("paypal", jobs)] == ["/job/Sydney/Engineer_R2"]
    assert workday._not_seen("paypal", [{"external_path": "/job/Sydney/Engineer_R2"}]) == []  # seen this run

    fetched = []
    monkeypatch.setattr(workday, "workday_detail", lambda emp, path: fetched.append(path) or
                        {"jobPostingInfo": {"jobDescription": "<p>x</p>", "externalUrl": "https://x" + path}})
    out = workday.fetch_details({"name": "PayPal"}, [{"external_path": f"/job/{i}"} for i in range(7)])
    assert sorted(fetched) == sorted(f"/job/{i}" for i in range(7)) and all(j["apply_url"] for j in out)
    database.close_connection(tmp_path / "jobs.db")

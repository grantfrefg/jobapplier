"""UK job sources: Reed's official API, Hays UK and Randstad UK, and the UK search settings."""

import httpx
import yaml

from applypilot import database
from applypilot.discovery import reed
from applypilot.discovery import smartextract as se
from applypilot.wizard.init import build_search_yaml

SEARCH = {"results": [
    {"jobId": 101, "employerName": "Monzo", "jobTitle": "Data Analyst", "locationName": "London",
     "minimumSalary": 50000, "maximumSalary": 60000, "jobDescription": "Short...", "applications": 37,
     "jobUrl": "https://www.reed.co.uk/jobs/data-analyst/101"},
    {"jobId": 102, "employerName": "Acme", "jobTitle": "Data Analyst", "locationName": "Manchester",
     "jobUrl": "https://www.reed.co.uk/jobs/data-analyst/102"},
]}
DETAIL = {"jobDescription": "<p>We build banking.</p><ul><li>SQL and Python every day</li></ul>" + "x" * 300,
          "externalUrl": "https://monzo.com/careers/101", "contractType": "Contract", "fullTime": True,
          "partTime": False}


def _client(seen):
    def handler(request):
        seen.append(request)
        assert request.headers["authorization"].startswith("Basic ")  # the key as the username
        body = DETAIL if request.url.path.endswith("/jobs/101") else SEARCH
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reed_jobs_come_with_full_descriptions_and_applicant_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("REED_API_KEY", "key-123")
    seen = []
    conn = database.init_db(tmp_path / "t.db")
    with _client(seen) as client:
        jobs = reed.search(client, "Data Analyst", "London, England", "contract")
        assert [r.url.params.get("contract") or r.url.params.get("temp") for r in seen] == ["true", "true"]
        assert seen[0].url.params["locationName"] == "London" and len(jobs) == 2  # de-duplicated
        reed.store(conn, jobs[:1], client)
    row = dict(conn.execute("SELECT * FROM jobs").fetchone())
    assert row["site"] == "Reed" and row["salary"] == "£50,000-£60,000" and row["applicants"] == "37 applicants"
    assert "SQL and Python every day" in row["full_description"] and "<" not in row["full_description"]
    assert row["application_url"] == "https://monzo.com/careers/101"
    assert row["board_job_type"] == "contract,fulltime"  # Reed's own job type, from the job's details
    database.close_connection(tmp_path / "t.db")


def test_reed_needs_a_uk_search_and_a_key(monkeypatch):
    assert reed.run_reed_discovery({"country": "australia", "queries": [{"query": "x"}]}) == \
        {"skipped": "not a UK search"}
    monkeypatch.setenv("REED_API_KEY", "")
    assert reed.run_reed_discovery({"country": "uk", "queries": [{"query": "x"}]}) == {"skipped": "no Reed API key"}


def test_uk_searches_use_uk_boards():
    cfg = yaml.safe_load(build_search_yaml("London", ["Data Analyst"], "uk", True))
    assert cfg["sites"] == ["indeed", "linkedin"] and cfg["smart_extract_sites"] == ["Hays UK", "Randstad UK"]
    assert {"location": "United Kingdom", "remote": True} in cfg["locations"]
    assert "Canary Wharf" in cfg["location_accept"]
    sites = [s for s in se.load_sites() if s["name"] in cfg["smart_extract_sites"]]
    urls = {t["name"]: t["url"] for t in se.build_scrape_targets(sites, cfg)}
    assert urls["Hays UK"] == "https://www.hays.co.uk/job-search?q=Data+Analyst&location=London"
    assert urls["Randstad UK"] == "https://www.randstad.co.uk/jobs/q-data-analyst/"

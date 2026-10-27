"""Application links: JobSpy's missing links must not become the text "None"."""

import math
import sqlite3

import pandas as pd
import pytest

from applypilot import database
from applypilot.discovery import jobspy as js


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    c = database.init_db(path)
    yield c
    database.close_connection(path)


def test_jobspy_keeps_only_real_application_links(conn):
    df = pd.DataFrame([
        {"job_url": "https://linkedin.com/jobs/view/1", "title": "A", "site": "linkedin", "job_url_direct": None},
        {"job_url": "https://linkedin.com/jobs/view/2", "title": "B", "site": "linkedin", "job_url_direct": math.nan},
        {"job_url": "https://au.indeed.com/viewjob?jk=3", "title": "C", "site": "indeed",
         "job_url_direct": "/apply/3"},
        {"job_url": "https://au.indeed.com/viewjob?jk=4", "title": "D", "site": "indeed",
         "job_url_direct": "https://careers.acme.com.au/jobs/4"},
    ])
    js.store_jobspy_results(conn, df, "test")
    links = dict(conn.execute("SELECT title, application_url FROM jobs").fetchall())
    assert links == {"A": None, "B": None, "C": None, "D": "https://careers.acme.com.au/jobs/4"}


def test_existing_bad_links_are_cleaned_on_startup(tmp_path):
    """Databases from before the fix hold the text 'None'; init_db clears it and resolves relative links."""
    path = tmp_path / "old.db"
    database.init_db(path)
    raw = sqlite3.connect(path)
    raw.executemany("INSERT INTO jobs (url, application_url) VALUES (?, ?)", [
        ("https://linkedin.com/jobs/view/1", "None"),
        ("https://au.jora.com/job/2", "/job/rd/2"),
        ("https://au.indeed.com/viewjob?jk=3", "https://careers.acme.com.au/jobs/3"),
    ])
    raw.commit()
    raw.close()
    database.close_connection(path)

    conn = database.init_db(path)
    links = dict(conn.execute("SELECT url, application_url FROM jobs").fetchall())
    database.close_connection(path)
    assert links == {
        "https://linkedin.com/jobs/view/1": None,
        "https://au.jora.com/job/2": "https://au.jora.com/job/rd/2",  # Jora's redirect to the employer
        "https://au.indeed.com/viewjob?jk=3": "https://careers.acme.com.au/jobs/3",
    }


@pytest.mark.parametrize("application_url, expected", [
    ("https://careers.acme.com.au/jobs/3", "https://careers.acme.com.au/jobs/3"),
    (None, "https://linkedin.com/jobs/view/1"),
    ("None", "https://linkedin.com/jobs/view/1"),
    ("/apply/3", "https://linkedin.com/apply/3"),  # relative: on the listing's site
    ("apply/3", "https://linkedin.com/jobs/view/1"),
])
def test_apply_link(application_url, expected, conn):
    job = {"url": "https://linkedin.com/jobs/view/1", "application_url": application_url}
    assert database.apply_link(job) == expected
    # sqlite3.Row works too (launcher passes rows)
    conn.execute("INSERT INTO jobs (url) VALUES (?)", (job["url"],))
    row = conn.execute("SELECT url, ? AS application_url FROM jobs", (application_url,)).fetchone()
    assert database.apply_link(row) == expected


def test_relative_application_link_resolves_against_listing():
    from applypilot.database import apply_link
    job = {"url": "https://au.jora.com/job/Data-Scientist-abc?sp=serp", "application_url": "/job/rd/abc?asp=jdv"}
    assert apply_link(job) == "https://au.jora.com/job/rd/abc?asp=jdv"
    assert apply_link({"url": "https://x.com/j", "application_url": "None"}) == "https://x.com/j"


def test_init_db_keeps_relative_links_as_absolute(tmp_path):
    from applypilot import database
    db = tmp_path / "links.db"
    conn = database.init_db(db)
    conn.executemany("INSERT INTO jobs (url, application_url) VALUES (?, ?)", [
        ("https://au.jora.com/job/a", "/job/rd/a"), ("https://x.com/b", "None")])
    conn.commit()
    conn = database.init_db(db)
    rows = dict(conn.execute("SELECT url, application_url FROM jobs").fetchall())
    assert rows == {"https://au.jora.com/job/a": "https://au.jora.com/job/rd/a", "https://x.com/b": None}
    database.close_connection(db)


@pytest.mark.parametrize("url, listing", [
    ("https://au.jora.com/job/Data-Scientist-1bbb311f38c36?sp=serp", True),
    ("https://au.jora.com/job/rd/1bbb311f38c36?asp=jdv", False),
    ("https://www.seek.com.au/job/81234567", True),
    ("https://www.seek.com.au/job/81234567/apply", False),
    ("https://au.indeed.com/viewjob?jk=abc", True),
    ("https://www.linkedin.com/jobs/view/123456/", True),
    ("https://boards.greenhouse.io/acme/jobs/1", False),
])
def test_is_job_listing(url, listing):
    from applypilot.apply.local_engine import _is_job_listing
    assert _is_job_listing(url) is listing


def test_protocol_relative_link_is_kept():
    assert database._absolute_link("https://au.jora.com/job/1", "//employer.com/apply") == "https://employer.com/apply"


def _link(text, href):
    return {"tag": "a", "text": text, "href": href}


@pytest.mark.parametrize("controls, expected", [
    # A similar job's "Quick apply" and a help link come before the ad's own button
    ([_link("How to apply", "https://au.jora.com/help"),
      _link("Quick apply", "https://au.jora.com/job/Other-Role-abc"),
      _link("Apply on employer site", "https://au.jora.com/job/rd/xyz")], "https://au.jora.com/job/rd/xyz"),
    ([_link("Apply", "https://www.seek.com.au/job/123/apply"),
      _link("Apply", "https://www.seek.com.au/job/999/apply")], "https://www.seek.com.au/job/123/apply"),
    # No board link: the first link whose text starts with Apply
    ([_link("How to apply", "https://x.com/help"), _link("Apply now", "https://employer.com/careers/1")],
     "https://employer.com/careers/1"),
    ([_link("How to apply", "https://x.com/help"), _link("Applying tips", "https://x.com/tips")], None),
])
def test_find_apply_link_prefers_the_ads_own_button(controls, expected):
    from applypilot.apply import local_engine as le
    assert le._find_apply_link(controls) == expected

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
    """Databases from before the fix hold the text 'None'; init_db clears it."""
    path = tmp_path / "old.db"
    database.init_db(path)
    raw = sqlite3.connect(path)
    raw.executemany("INSERT INTO jobs (url, application_url) VALUES (?, ?)", [
        ("https://linkedin.com/jobs/view/1", "None"),
        ("https://au.jora.com/job/2", "/job/2"),
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
        "https://au.jora.com/job/2": None,
        "https://au.indeed.com/viewjob?jk=3": "https://careers.acme.com.au/jobs/3",
    }


@pytest.mark.parametrize("application_url, expected", [
    ("https://careers.acme.com.au/jobs/3", "https://careers.acme.com.au/jobs/3"),
    (None, "https://linkedin.com/jobs/view/1"),
    ("None", "https://linkedin.com/jobs/view/1"),
    ("/apply/3", "https://linkedin.com/jobs/view/1"),
])
def test_apply_link(application_url, expected, conn):
    job = {"url": "https://linkedin.com/jobs/view/1", "application_url": application_url}
    assert database.apply_link(job) == expected
    # sqlite3.Row works too (launcher passes rows)
    conn.execute("INSERT INTO jobs (url) VALUES (?)", (job["url"],))
    row = conn.execute("SELECT url, ? AS application_url FROM jobs", (application_url,)).fetchone()
    assert database.apply_link(row) == expected

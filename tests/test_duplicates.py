"""Cross-site de-duplication: one role listed on several boards is applied to once."""

import pytest

from applypilot import config, database

DESC = (
    "We are looking for a Data Analyst to join our Sydney analytics team. You will build "
    "dashboards in Power BI, write SQL against our Snowflake warehouse, partner with finance "
    "and operations stakeholders, automate recurring reporting, and present insights to the "
    "leadership group every month. You have three or more years of experience with SQL, "
    "strong communication skills, and experience in healthcare or insurance is a plus. "
    "Hybrid working, two days in our Barangaroo office, competitive salary and benefits."
)
OTHER_DESC = (
    "Join a fast growing retail business as a Data Analyst. You will own merchandise "
    "planning reports, forecast weekly sales by category, maintain Excel models, support "
    "buyers with ad hoc analysis, and help migrate legacy reports into Tableau. Experience "
    "with retail or ecommerce data, Python is a bonus, and you love working with numbers "
    "in a busy environment with tight deadlines and many stakeholders across the business."
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "load_search_config", lambda: {})
    c = database.init_db(db)
    yield c
    database.close_connection(db)


def add(conn, url, site, title="Data Analyst", company=None, desc=None, **extra):
    cols = {"url": url, "site": site, "title": title, "company": company, "full_description": desc, **extra}
    conn.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 list(cols.values()))
    conn.commit()


def dup_map(conn):
    return {r["url"]: r["duplicate_of"] for r in conn.execute("SELECT url, duplicate_of FROM jobs")}


def test_same_company_and_title_across_sites_keeps_preferred_site(conn):
    add(conn, "https://au.jora.com/job/1", "Jora", company="Acme Health")
    add(conn, "https://au.indeed.com/viewjob?jk=2", "indeed", company="ACME HEALTH PTY LTD")
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme Health Group")
    assert database.mark_duplicates(conn) == 2
    canon = "https://linkedin.com/jobs/view/3"
    assert dup_map(conn) == {"https://au.jora.com/job/1": canon,
                             "https://au.indeed.com/viewjob?jk=2": canon, canon: None}
    assert database.mark_duplicates(conn) == 0  # idempotent


def test_site_priority_is_configurable(conn, monkeypatch):
    monkeypatch.setattr(config, "load_search_config", lambda: {"site_priority": ["jora", "linkedin"]})
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme")
    add(conn, "https://au.jora.com/job/1", "Jora", company="Acme")
    database.mark_duplicates(conn)
    assert dup_map(conn)["https://linkedin.com/jobs/view/3"] == "https://au.jora.com/job/1"


def test_already_applied_listing_stays_canonical(conn):
    add(conn, "https://au.jora.com/job/1", "Jora", company="Acme", applied_at="2026-10-01",
        apply_status="applied")
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme",
        tailored_resume_path="/x.txt", fit_score=9)
    database.mark_duplicates(conn)
    assert dup_map(conn)["https://linkedin.com/jobs/view/3"] == "https://au.jora.com/job/1"


def test_unknown_company_matched_by_description(conn):
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme Health", desc=DESC)
    # Jora often has no company; its copy of the ad is reformatted but the same text
    add(conn, "https://au.jora.com/job/1", "Jora", desc="Data Analyst\n\n" + DESC.upper() + " Apply now.")
    add(conn, "https://au.jora.com/job/9", "Jora", desc=OTHER_DESC)
    database.mark_duplicates(conn)
    d = dup_map(conn)
    assert d["https://au.jora.com/job/1"] == "https://linkedin.com/jobs/view/3"
    assert d["https://au.jora.com/job/9"] is None


def test_different_companies_same_title_are_different_roles(conn):
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme", desc=DESC)
    add(conn, "https://au.indeed.com/viewjob?jk=2", "indeed", company="Globex", desc=OTHER_DESC)
    add(conn, "https://au.indeed.com/viewjob?jk=4", "indeed", company="Initech")  # no description
    database.mark_duplicates(conn)
    assert set(dup_map(conn).values()) == {None}


def test_same_employer_apply_link_is_same_role(conn):
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", title="Data Analyst",
        application_url="https://careers.acme.com.au/jobs/123?src=linkedin")
    add(conn, "https://au.indeed.com/viewjob?jk=2", "indeed", title="Analyst, Data",
        application_url="https://www.careers.acme.com.au/jobs/123/?utm=indeed")
    database.mark_duplicates(conn)
    assert dup_map(conn)["https://au.indeed.com/viewjob?jk=2"] == "https://linkedin.com/jobs/view/3"


def test_pipeline_stages_skip_duplicates(conn):
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme", desc=DESC)
    add(conn, "https://au.indeed.com/viewjob?jk=2", "indeed", company="Acme", desc=DESC)
    database.mark_duplicates(conn)
    pending = database.get_jobs_by_stage(conn, "pending_score")
    assert [j["url"] for j in pending] == ["https://linkedin.com/jobs/view/3"]
    assert database.get_stats(conn)["duplicates"] == 1


def test_never_applies_twice(conn):
    """A role applied to on Jora must not be offered again via its LinkedIn listing."""
    from applypilot.apply import launcher

    add(conn, "https://au.jora.com/job/1", "Jora", company="Acme", applied_at="2026-10-01",
        apply_status="applied", tailored_resume_path="/a.txt", fit_score=9)
    # Discovered later, already tailored before the duplicate was noticed
    add(conn, "https://linkedin.com/jobs/view/3", "linkedin", company="Acme",
        tailored_resume_path="/b.txt", fit_score=9)
    assert launcher.acquire_job(min_score=7) is None
    assert launcher.acquire_job(target_url="https://linkedin.com/jobs/view/3") is None

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


JORA, INDEED, LINKEDIN = "https://au.jora.com/job/1", "https://au.indeed.com/viewjob?jk=2", "https://linkedin.com/jobs/view/3"


def _redo_marks(conn):
    return {r[0] for r in conn.execute("SELECT url FROM jobs WHERE redo_requested = 1")}


def test_redo_keeps_documents_and_reprioritises_sites(conn):
    # The Jora listing was found and tailored first, so it was the canonical copy
    add(conn, JORA, "Jora", company="Acme", desc=DESC, tailored_resume_path="/t/jora.txt",
        cover_letter_path="/c/jora.txt", tailor_attempts=1, fit_score=8)
    add(conn, INDEED, "indeed", company="Acme", desc=DESC, fit_score=8)
    add(conn, LINKEDIN, "linkedin", company="Acme", desc=DESC, fit_score=8)
    database.mark_duplicates(conn)
    assert dup_map(conn)[LINKEDIN] == JORA

    assert database.redo_documents(conn) == 1
    row = conn.execute("SELECT tailored_resume_path, cover_letter_path FROM jobs WHERE url = ?", (JORA,)).fetchone()
    assert tuple(row) == ("/t/jora.txt", "/c/jora.txt")  # nothing deleted
    # LinkedIn now leads, so it carries the Redo mark and is the one rewritten
    assert dup_map(conn) == {LINKEDIN: None, INDEED: LINKEDIN, JORA: LINKEDIN}
    assert _redo_marks(conn) == {LINKEDIN}
    assert [j["url"] for j in database.get_jobs_by_stage(conn, "pending_redo")] == [LINKEDIN]

    # If LinkedIn's rewrite doesn't pass, the mark goes and Jora's documents are used again
    database.finish_redo(conn, LINKEDIN)
    database.mark_duplicates(conn)
    assert dup_map(conn)[LINKEDIN] == JORA and _redo_marks(conn) == set()


def test_redo_keeps_applied_jobs_and_frees_stuck_ones(conn):
    add(conn, JORA, "Jora", title="Data Analyst", tailored_resume_path="/t/a.txt", applied_at="2026-10-01")
    add(conn, INDEED, "indeed", title="Data Engineer", tailored_resume_path="/t/b.txt", apply_status="in_progress",
        agent_id="worker-0")  # left over from an apply run that never finished
    add(conn, LINKEDIN, "linkedin", title="ML Engineer", tailored_resume_path="/t/c.txt")
    assert database.redo_documents(conn) == 2
    assert _redo_marks(conn) == {INDEED, LINKEDIN}
    row = conn.execute("SELECT apply_status, agent_id FROM jobs WHERE url = ?", (INDEED,)).fetchone()
    assert tuple(row) == (None, None)  # back in the apply queue


def test_redo_skips_the_job_being_applied_to(conn):
    add(conn, INDEED, "indeed", title="Data Engineer", tailored_resume_path="/t/b.txt", apply_status="in_progress")
    add(conn, LINKEDIN, "linkedin", title="ML Engineer", tailored_resume_path="/t/c.txt")
    assert database.redo_documents(conn, applying_now=INDEED) == 1
    row = conn.execute("SELECT apply_status, redo_requested FROM jobs WHERE url = ?", (INDEED,)).fetchone()
    assert tuple(row) == ("in_progress", 0)


def test_redo_one_job_includes_its_other_listings(conn):
    add(conn, JORA, "Jora", company="Acme", desc=DESC, tailored_resume_path="/t/jora.txt")
    add(conn, LINKEDIN, "linkedin", company="Acme", desc=DESC)
    add(conn, INDEED, "indeed", title="Platform Engineer", company="Other", desc=OTHER_DESC,
        tailored_resume_path="/t/other.txt")
    database.mark_duplicates(conn)
    assert database.redo_documents(conn, url=LINKEDIN) == 1  # a duplicate's url reaches its canonical copy
    assert _redo_marks(conn) == {LINKEDIN}                  # the preferred listing of that role only
    assert database.redo_documents(conn, url="https://nowhere") == 0

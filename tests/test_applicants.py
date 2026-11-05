"""How many people have applied to a job, when the board says (applicants.py)."""

import pandas as pd
import pytest

from applypilot import applicants, database
from applypilot.discovery import jobspy


@pytest.mark.parametrize("page, found", [
    ('<span class="num-applicants__caption">\n  47 applicants\n</span>', ("47 applicants", 47)),
    ('<figcaption class="num-applicants__caption">Over 200 applicants</figcaption>', ("200+ applicants", 200)),
    ("Be among the first 25 applicants · Promoted", ("Under 25 applicants", 12)),
    ("1,234 applicants", ("1234 applicants", 1234)),
    ("100+ applicants", ("100+ applicants", 100)),
    ("Apply now. We review every application.", None),
])
def test_counts_are_read_from_the_page(page, found):
    assert applicants.parse(page) == found


def test_linkedin_pages_jobspy_fetches_give_the_count(monkeypatch, tmp_path):
    from jobspy.linkedin import LinkedIn

    class Response:
        text = '<span class="num-applicants__caption">Over 100 applicants</span>'

    class Session:
        def get(self, url):
            return Response()

    def original(self, job_id):  # JobSpy's own fetch: requests the page, returns other fields
        self.session.get(f"https://www.linkedin.com/jobs/view/{job_id}")
        return {"description": "x"}
    monkeypatch.setattr(LinkedIn, "_fetch_details", original)
    applicants.watch_linkedin()
    scraper = type("Scraper", (), {"session": Session()})()
    assert LinkedIn._fetch_details(scraper, "4012345678") == {"description": "x"}  # JobSpy's result unchanged
    assert applicants.for_url("https://www.linkedin.com/jobs/view/4012345678") == ("100+ applicants", 100)

    conn = database.init_db(tmp_path / "t.db")
    df = pd.DataFrame([{"job_url": "https://www.linkedin.com/jobs/view/4012345678", "title": "Analyst",
                        "site": "linkedin", "description": "short"}])
    jobspy.store_jobspy_results(conn, df, "linkedin")
    row = conn.execute("SELECT applicants, applicants_count FROM jobs").fetchone()
    assert tuple(row) == ("100+ applicants", 100)
    database.close_connection(tmp_path / "t.db")


def test_jobspy_job_type_is_stored(tmp_path):
    """JobSpy's own job type is kept (1.4 gives a list of JobType; older versions a string)."""
    from jobspy.model import JobType
    assert jobspy.board_job_type([JobType.FULL_TIME, JobType.CONTRACT]) == "fulltime,contract"
    assert jobspy.board_job_type("fulltime, contract") == "fulltime,contract"
    assert jobspy.board_job_type(float("nan")) is None and jobspy.board_job_type(None) is None
    assert jobspy.board_job_type([]) is None
    conn = database.init_db(tmp_path / "t.db")
    url = "https://au.indeed.com/viewjob?jk=1"
    jobspy.store_jobspy_results(conn, pd.DataFrame([{"job_url": url, "title": "Analyst", "job_type": None}]),
                                "indeed")
    assert conn.execute("SELECT board_job_type FROM jobs").fetchone()[0] is None
    # Seen again with a type: a job found before types were stored gets it
    df = pd.DataFrame([{"job_url": url, "title": "Analyst", "job_type": [JobType.TEMPORARY]}])
    jobspy.store_jobspy_results(conn, df, "indeed")
    assert conn.execute("SELECT board_job_type FROM jobs").fetchone()[0] == "temporary"
    database.close_connection(tmp_path / "t.db")

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

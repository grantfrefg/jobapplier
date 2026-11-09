"""Recruiter screen (simulated): an employer-side rating of the base and tailored resume. It only
reports: nothing it finds feeds back into tailoring."""

import json
import re
from pathlib import Path

import pytest

from applypilot.scoring import screen

BASE = "MORGAN LEE\nBusiness Analyst\n- Wrote user stories in Jira."
TAILORED = "MORGAN LEE\nBusiness Analyst\n- Wrote 300+ user stories in Jira; mapped processes in BPMN."
JOB = {"url": "u1", "title": "Business Analyst - Salesforce", "company": "Coralbay", "location": "Sydney",
       "full_description": "Salesforce BA. Salesforce Administrator certification essential. User stories, BPMN."}


class FakeAI:
    model = "fake-model"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        self.seen.append(messages)
        return self.replies.pop(0)


def _reply(score, not_shown=("Salesforce Administrator certification",)):
    return {"score": score, "not_shown": list(not_shown),
            "noticed": ["Clear BA track record", "No Salesforce", "Banking domain", "a fourth point"]}


@pytest.mark.parametrize("score, verdict", [(10, "advance"), (7, "advance"), (6, "maybe"), (5, "maybe"),
                                            (4, "reject"), (1, "reject")])
def test_the_verdict_follows_the_score(score, verdict):
    assert screen.verdict(score) == verdict


def test_one_screen_reads_only_the_ad_and_the_resume(monkeypatch):
    ai = FakeAI([_reply(6)])
    monkeypatch.setattr(screen, "get_client", lambda: ai)
    got = screen.screen(TAILORED, JOB)
    assert got == {"score": 6, "verdict": "maybe", "not_shown": ["Salesforce Administrator certification"],
                   "noticed": ["Clear BA track record", "No Salesforce", "Banking domain"]}
    system, user = ai.seen[0][0]["content"], ai.seen[0][1]["content"]
    assert "screener" in system and "work rights" in system
    assert "Salesforce Administrator certification essential" in user and "300+ user stories" in user
    # The candidate's preferences (pay, visas, the fit score's facts) are never sent
    assert "CANDIDATE FACTS" not in user and "salary" not in user.lower()


@pytest.mark.parametrize("bad", [{"score": "high"}, {"score": 0}, {"score": 11}, {}, ["not", "a", "dict"]])
def test_a_reply_without_a_valid_score_is_an_error(monkeypatch, bad):
    monkeypatch.setattr(screen, "get_client", lambda: FakeAI([bad]))
    with pytest.raises(ValueError):
        screen.screen(TAILORED, JOB)


def test_run_screens_the_base_and_the_job_resume(monkeypatch):
    ai = FakeAI([_reply(8, ()), _reply(5)])
    monkeypatch.setattr(screen, "get_client", lambda: ai)
    report = screen.run(JOB, BASE, TAILORED)
    assert report["tailored"]["score"] == 8 and report["base"]["score"] == 5
    assert report["model"] == "fake-model" and report["resume_hash"] == screen.fingerprint(TAILORED)
    assert len(ai.seen) == 2 and "300+" in ai.seen[0][1]["content"] and "300+" not in ai.seen[1][1]["content"]
    # No base resume saved: just the job's one
    monkeypatch.setattr(screen, "get_client", lambda: FakeAI([_reply(7)]))
    assert screen.run(JOB, "", TAILORED)["base"] is None
    with pytest.raises(ValueError, match="ad"):
        screen.run({"url": "u2", "title": "x"}, BASE, TAILORED)
    with pytest.raises(ValueError, match="resume"):
        screen.run(JOB, BASE, "  ")


def test_tailoring_never_sees_the_screen():
    """A writer told to raise a screen score starts claiming things: the screen stays a report."""
    src = Path(screen.__file__).parent
    for name in ("tailor.py", "cover_letter.py", "scorer.py", "facts.py", "validator.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert not re.search(r"\bscreen\.|import screen|scoring\.screen|screen_report", text), name


# -- through the app -----------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from test_ui import _seed_job, client  # noqa: E402,F401


def test_screen_through_the_api_and_the_job_list(client, tmp_path, monkeypatch):  # noqa: F811
    from applypilot import config, database
    url = "https://example.com/job/1"
    _seed_job(tmp_path, url)
    config.RESUME_PATH.write_text(BASE, encoding="utf-8")
    [job] = client.get("/api/jobs?view=ready").json()
    assert job["screen"] is None

    # No ad text yet: refused before any AI request
    monkeypatch.setattr(screen, "get_client", lambda: FakeAI([]))
    r = client.post("/api/jobs/screen", json={"url": url})
    assert r.status_code == 400 and "ad" in r.json()["detail"]

    conn = database.get_connection()
    conn.execute("UPDATE jobs SET full_description = ? WHERE url = ?", (JOB["full_description"], url))
    conn.commit()
    ai = FakeAI([_reply(8, ()), _reply(5)])
    monkeypatch.setattr(screen, "get_client", lambda: ai)
    got = client.post("/api/jobs/screen", json={"url": url}).json()
    assert got["base"]["score"] == 5 and got["tailored"]["score"] == 8 and got["stale"] is False
    stored = json.loads(conn.execute("SELECT screen_report FROM jobs").fetchone()[0])
    assert stored["tailored"]["verdict"] == "advance"

    [job] = client.get("/api/jobs?view=ready").json()
    assert job["screen"]["tailored"]["score"] == 8 and job["screen"]["stale"] is False
    assert "own_resume_path" not in job

    # Editing the resume afterwards marks the screen as out of date
    (tmp_path / "tailored" / "acme.txt").write_text(TAILORED + "\n- Ran UAT.", encoding="utf-8")
    [job] = client.get("/api/jobs?view=ready").json()
    assert job["screen"]["stale"] is True

    # A failed AI request is reported, and the old screen is kept
    class Broken:
        model = "x"

        def chat_json(self, *a, **k):
            raise RuntimeError("quota used up")
    monkeypatch.setattr(screen, "get_client", lambda: Broken())
    r = client.post("/api/jobs/screen", json={"url": url})
    assert r.status_code == 502 and "quota" in r.json()["detail"]
    assert json.loads(conn.execute("SELECT screen_report FROM jobs").fetchone()[0])["tailored"]["score"] == 8
    assert client.post("/api/jobs/screen", json={"url": "nope"}).status_code == 404

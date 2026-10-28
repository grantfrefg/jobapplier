"""Running one pipeline step again (applypilot rerun / Run again in the web UI)."""

import json

import pytest

from applypilot import config, database, rerun

RESUME = "Jane Doe\nSUMMARY\nData scientist.\nSKILLS\nPython, SQL\nEXPERIENCE\nData Scientist, Acme\n- Built models\n"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    for name, value in {"APP_DIR": tmp_path, "RESUME_PATH": tmp_path / "resume.txt",
                        "PROFILE_PATH": tmp_path / "profile.json", "COVER_LETTER_DIR": tmp_path / "covers",
                        "TAILORED_DIR": tmp_path / "tailored"}.items():
        monkeypatch.setattr(config, name, value)
    (tmp_path / "resume.txt").write_text(RESUME)
    (tmp_path / "profile.json").write_text(json.dumps({"personal": {"full_name": "Jane Doe"}}))
    monkeypatch.setattr(config, "load_profile", lambda: {"personal": {"full_name": "Jane Doe"}})
    c = database.init_db(db)
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    (tailored / "acme.txt").write_text(RESUME)
    (tailored / "acme_CL.txt").write_text("Dear Hiring Manager,\nOld letter.\nKind regards,\nJane")
    rows = [  # url, score, description, resume, letter, applied
        ("https://ex.com/ready", 9, "Python SQL", str(tailored / "acme.txt"), str(tailored / "acme_CL.txt"), None),
        ("https://ex.com/scored", 5, "Excel", None, None, None),
        ("https://ex.com/new", None, None, None, None, None),
        ("https://ex.com/applied", 9, "Python", str(tailored / "acme.txt"), None, "2026-10-01"),
    ]
    for url, score, desc, resume, letter, applied in rows:
        c.execute("INSERT INTO jobs (url, title, site, fit_score, full_description, tailored_resume_path, "
                  "cover_letter_path, applied_at) VALUES (?, 'Data Scientist', 'linkedin', ?, ?, ?, ?, ?)",
                  (url, score, desc, resume, letter, applied))
    c.commit()
    yield c
    database.close_connection(db)


def urls(jobs):
    return sorted(j["url"].rsplit("/", 1)[1] for j in jobs)


def test_each_step_picks_the_jobs_it_can_run_on(conn):
    assert urls(rerun.targets(conn, "enrich")) == ["new", "ready", "scored"]  # never an applied job
    assert urls(rerun.targets(conn, "score")) == ["ready", "scored"]  # needs a description
    assert urls(rerun.targets(conn, "review")) == ["ready"]  # needs documents
    assert urls(rerun.targets(conn, "tailor", min_score=7)) == ["ready"]
    assert urls(rerun.targets(conn, "score", min_score=7)) == ["ready", "scored"]  # score itself ignores it
    assert urls(rerun.targets(conn, "score", ["https://ex.com/scored"], min_score=7)) == ["scored"]
    with pytest.raises(ValueError):
        rerun.targets(conn, "nonsense")


def test_score_again_keeps_the_old_score_until_a_new_one_comes(conn, monkeypatch):
    from applypilot.scoring import scorer
    monkeypatch.setattr(scorer, "load_search_config", lambda: {})
    monkeypatch.setattr(scorer, "RESUME_PATH", config.RESUME_PATH)
    monkeypatch.setattr(scorer, "load_profile", config.load_profile)
    monkeypatch.setattr(scorer, "score_batch", lambda resume, jobs, profile: {
        j["url"]: {"score": 8, "keywords": "Excel", "reasoning": "Better now"} for j in jobs})
    assert rerun.run("score", ["https://ex.com/scored"]) == 1
    assert conn.execute("SELECT fit_score FROM jobs WHERE url = 'https://ex.com/scored'").fetchone()[0] == 8
    assert conn.execute("SELECT fit_score FROM jobs WHERE url = 'https://ex.com/ready'").fetchone()[0] == 9

    def broken(*a):
        raise RuntimeError("API down")
    monkeypatch.setattr(scorer, "score_batch", broken)
    rerun.run("score", ["https://ex.com/scored"])
    assert conn.execute("SELECT fit_score FROM jobs WHERE url = 'https://ex.com/scored'").fetchone()[0] == 8


def test_review_again_flags_but_keeps_the_documents(conn, monkeypatch):
    from applypilot.scoring import tailor
    seen = {}

    def judge(original, tailored, title, profile, letter, job_ad=""):
        seen.update(letter=letter, ad=job_ad)
        return {"passed": False, "verdict": "FAIL", "issues": "Claims team leadership"}
    monkeypatch.setattr(tailor, "judge_tailored_resume", judge)
    rerun.run("review", ["https://ex.com/ready"])
    row = conn.execute("SELECT tailor_status, tailor_issues, tailored_resume_path FROM jobs "
                       "WHERE url = 'https://ex.com/ready'").fetchone()
    assert tuple(row)[:2] == ("review_failed", "Claims team leadership") and row[2]
    assert "Old letter" in seen["letter"] and seen["ad"] == "Python SQL"  # it read the letter and the ad

    monkeypatch.setattr(tailor, "judge_tailored_resume", lambda *a, **k: {"passed": True, "issues": "none"})
    rerun.run("review", ["https://ex.com/ready"])
    assert tuple(conn.execute("SELECT tailor_status, tailor_issues FROM jobs WHERE url = 'https://ex.com/ready'")
                 .fetchone()) == ("approved", None)


def test_cover_letter_again_replaces_it_only_when_a_new_one_passes(conn, monkeypatch, tmp_path):
    from applypilot.scoring import cover_letter
    rendered = []
    monkeypatch.setattr(rerun, "_render", rendered.append)
    monkeypatch.setattr(cover_letter, "generate_cover_letter", lambda *a, **k: "")
    rerun.run("cover", ["https://ex.com/ready"])
    letter = tmp_path / "tailored" / "acme_CL.txt"
    assert "Old letter" in letter.read_text() and not rendered

    monkeypatch.setattr(cover_letter, "generate_cover_letter",
                        lambda *a, **k: "Dear Hiring Manager,\nNew letter.\nKind regards,\nJane")
    rerun.run("cover", ["https://ex.com/ready"])
    assert "New letter" in letter.read_text() and rendered == [letter]


def test_ats_and_pdf_again(conn, monkeypatch):
    rendered = []
    monkeypatch.setattr(rerun, "_render", rendered.append)
    rerun.run("pdf", ["https://ex.com/ready"])
    assert [p.name for p in rendered] == ["acme.txt", "acme_CL.txt"]
    score = conn.execute("SELECT ats_score FROM jobs WHERE url = 'https://ex.com/ready'").fetchone()[0]
    assert isinstance(score, int)  # the PDF step redoes the ATS check, which reads the PDF
    conn.execute("UPDATE jobs SET ats_score = NULL")
    conn.commit()
    rerun.run("ats", min_score=7)
    assert conn.execute("SELECT ats_score FROM jobs WHERE url = 'https://ex.com/ready'").fetchone()[0] == score


def test_discover_again_retries_only_the_failed_searches(conn, monkeypatch):
    from applypilot.discovery import jobspy, retry, smartextract
    calls = []
    monkeypatch.setattr(jobspy, "run_discovery", lambda retry_failed=False: calls.append(("jobspy", retry_failed)))
    monkeypatch.setattr(smartextract, "run_smart_extract",
                        lambda retry_failed=False: calls.append(("jora", retry_failed)))
    assert rerun.run("discover") == 0 and calls == []  # nothing failed last time

    retry.save("jobspy", [{"query": "A", "location": "Sydney", "sites": ["linkedin"]}])
    assert rerun.run("discover") == 1
    assert calls == [("jobspy", True)]  # Jora had no failures, so it isn't searched
    with pytest.raises(ValueError):
        rerun.targets(conn, "discover")

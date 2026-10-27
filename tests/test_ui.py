"""Tests for the local web UI (applypilot ui)."""

import json
import os
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from applypilot import config, database  # noqa: E402
from applypilot.apply import local_engine as le  # noqa: E402

from test_local_engine import FORM_HTML, PROFILE, RESUME, _fake_gemini  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name, value in {
        "APP_DIR": tmp_path, "ENV_PATH": tmp_path / ".env", "PROFILE_PATH": tmp_path / "profile.json",
        "RESUME_PATH": tmp_path / "resume.txt", "RESUME_PDF_PATH": tmp_path / "resume.pdf",
        "SEARCH_CONFIG_PATH": tmp_path / "searches.yaml", "DB_PATH": tmp_path / "test.db",
        "TAILORED_DIR": tmp_path / "tailored", "COVER_LETTER_DIR": tmp_path / "covers",
        "LOG_DIR": tmp_path / "logs", "CHROME_WORKER_DIR": tmp_path / "cw",
        "APPLY_WORKER_DIR": tmp_path / "aw", "LOCAL_BROWSER_PROFILE_DIR": tmp_path / "browser",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    for key in ("EXECUTION_ENGINE", "GEMINI_API_KEY", "LLM_MODEL", "OPENAI_API_KEY", "LLM_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "load_env", lambda: None)  # don't read the developer's real .env

    from applypilot.ui.server import create_app
    with TestClient(create_app()) as c:
        yield c
    database.close_connection(tmp_path / "test.db")


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ApplyPilot" in r.text


def test_settings_roundtrip_masks_secrets(client):
    r = client.post("/api/settings", json={"EXECUTION_ENGINE": "local_playwright",
                                           "GEMINI_API_KEY": "AIza-test-key-1234", "LLM_MODEL": ""})
    body = r.json()
    assert body["EXECUTION_ENGINE"] == "local_playwright"
    assert body["GEMINI_API_KEY"] == {"set": True, "hint": "…1234"}
    assert "AIza" not in r.text
    assert os.environ["EXECUTION_ENGINE"] == "local_playwright"
    assert "GEMINI_API_KEY=AIza-test-key-1234" in config.ENV_PATH.read_text()

    # Blank secret keeps the saved one; explicit clear removes it
    client.post("/api/settings", json={"GEMINI_API_KEY": ""})
    assert client.get("/api/settings").json()["GEMINI_API_KEY"]["set"] is True
    client.post("/api/settings", json={"clear": ["GEMINI_API_KEY"]})
    assert client.get("/api/settings").json()["GEMINI_API_KEY"]["set"] is False

    assert client.post("/api/settings", json={"EXECUTION_ENGINE": "nope"}).status_code == 400
    assert client.get("/api/status").json()["engine"] == "local_playwright"


def test_profile_resume_searches(client):
    template = client.get("/api/profile").json()
    assert template["exists"] is False and "personal" in template["profile"]
    assert client.post("/api/profile", json={"personal": {"full_name": ""}}).status_code == 400
    saved = client.post("/api/profile", json=PROFILE).json()
    assert saved["exists"] and saved["profile"]["personal"]["full_name"] == "Jane Quinn Doe"
    # Missing template keys are filled in for the form
    assert "eeo_voluntary" in saved["profile"]

    r = client.post("/api/resume", data={"text": RESUME},
                    files={"pdf": ("cv.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.json() == {"text": RESUME.strip() + "\n", "has_pdf": True}
    assert client.post("/api/resume", files={"pdf": ("x.pdf", b"nope", "application/pdf")}).status_code == 400

    s = client.post("/api/searches", json={"location": "Sydney, NSW", "country": "australia",
                                           "include_remote": True, "roles": ["Data Analyst", " "]}).json()
    assert s["simple"] == {"location": "Sydney, NSW", "country": "australia", "include_remote": True,
                           "exclude_titles": [], "roles": ["Data Analyst"]}
    s = client.post("/api/searches", json={"location": "Sydney, NSW", "roles": ["Data Analyst"],
                                           "exclude_titles": "intern, Senior Director,"}).json()
    assert s["simple"]["exclude_titles"] == ["intern", "Senior Director"]
    assert client.post("/api/searches", json={"location": "", "roles": ["x"]}).status_code == 400
    assert client.post("/api/searches", json={"yaml": "queries: [oops"}).status_code == 400

    checks = client.get("/api/status").json()["checks"]
    assert checks["profile"] and checks["resume_txt"] and checks["resume_pdf"] and checks["searches"]


def test_pipeline_runs_subprocess(client):
    client.post("/api/pipeline", json={"stages": ["bogus-is-dropped", "pdf"]})
    for _ in range(600):  # cold Python start on Windows CI can take a while
        state = client.get("/api/pipeline").json()
        if not state["running"]:
            break
        time.sleep(0.1)
    assert not state["running"], state
    assert state["log"][0] == "$ applypilot run pdf --min-score 7"
    assert state["returncode"] is not None  # never "finished" without an exit code


def _seed_job(tmp_path, url):
    tailored = tmp_path / "tailored"
    tailored.mkdir(exist_ok=True)
    resume = tailored / "acme.txt"
    resume.write_text(RESUME)
    resume.with_suffix(".pdf").write_bytes(b"%PDF-1.4 fake")
    conn = database.get_connection()
    conn.execute("""INSERT INTO jobs (url, title, site, location, fit_score, application_url,
                    tailored_resume_path) VALUES (?, 'Engineer', 'Acme', 'Sydney', 9, ?, ?)""",
                 (url, url, str(resume)))
    conn.commit()


def test_jobs_listing_and_files(client, tmp_path):
    _seed_job(tmp_path, "https://example.com/job/1")
    jobs = client.get("/api/jobs?view=ready").json()
    assert [j["title"] for j in jobs] == ["Engineer"] and jobs[0]["tailored"] == 1
    r = client.get("/api/jobs/file", params={"url": "https://example.com/job/1", "kind": "resume"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get("/api/jobs?view=applied").json() == []


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")
def test_apply_through_web_gate(client, tmp_path, monkeypatch):
    form = tmp_path / "form.html"
    form.write_text(FORM_HTML)
    url = form.as_uri()
    _seed_job(tmp_path, url)
    client.post("/api/profile", json=PROFILE)
    client.post("/api/settings", json={"EXECUTION_ENGINE": "local_playwright", "GEMINI_API_KEY": "k-12345678"})
    prompts: list[str] = []
    monkeypatch.setattr(le, "_call_gemini", _fake_gemini(prompts))

    # Nothing to approve yet
    assert client.post("/api/apply/decision", json={"decision": "applied"}).status_code == 409

    assert client.post("/api/apply", json={"url": url}).json()["mode"] == "local"

    def wait_for(state):
        for _ in range(300):
            s = client.get("/api/apply").json()
            if s["state"] == state:
                return s
            assert s["state"] != "error", s["message"]
            time.sleep(0.1)
        raise AssertionError(f"never reached {state}: {s}")

    s = wait_for("waiting")
    assert s["job"]["title"] == "Engineer"
    # The files to upload by hand, in a visible folder holding only this job's documents
    assert [f["name"] for f in s["files"]] == ["Jane_Quinn_Doe_Resume.pdf"]
    assert s["files"][0]["path"].startswith(s["upload_dir"])
    assert client.post("/api/apply", json={"url": url}).status_code == 409  # one at a time

    # Re-scan goes back to filling, then waits again
    client.post("/api/apply/decision", json={"decision": "rescan"})
    wait_for("waiting")
    assert len(prompts) == 2  # step 2, then step 2 again (step 1 is all standard fields: no AI)

    client.post("/api/apply/decision", json={"decision": "applied"})
    s = wait_for("done")
    assert s["result"] == "applied"

    row = database.get_connection().execute(
        "SELECT apply_status, applied_at FROM jobs WHERE url = ?", (url,)).fetchone()
    assert row["apply_status"] == "applied" and row["applied_at"]
    assert client.get("/api/jobs?view=applied").json()[0]["url"] == url
    assert json.dumps(prompts).count("Jane") == 0


def test_launcher_importable_from_worker_thread():
    """The UI first imports launcher from a worker thread; that must not crash."""
    import subprocess
    import sys
    code = (
        "import threading, traceback\n"
        "err = []\n"
        "def go():\n"
        "    try:\n"
        "        import applypilot.apply.launcher\n"
        "    except Exception:\n"
        "        err.append(traceback.format_exc())\n"
        "t = threading.Thread(target=go); t.start(); t.join()\n"
        "print(err[0] if err else 'OK')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().endswith("OK"), out.stdout + out.stderr


def test_duplicate_listing_hidden_and_refused(client, tmp_path):
    _seed_job(tmp_path, "https://linkedin.com/jobs/view/1")
    _seed_job(tmp_path, "https://au.jora.com/job/1")
    conn = database.get_connection()
    conn.execute("UPDATE jobs SET company = 'Acme', site = 'linkedin' WHERE url LIKE '%linkedin%'")
    conn.execute("UPDATE jobs SET company = 'Acme Pty Ltd', site = 'Jora' WHERE url LIKE '%jora%'")
    conn.commit()

    ready = client.get("/api/jobs?view=ready").json()
    assert [j["url"] for j in ready] == ["https://linkedin.com/jobs/view/1"]
    assert ready[0]["also_on"] == "Jora"
    everything = {j["url"]: j for j in client.get("/api/jobs?view=all").json()}
    assert everything["https://au.jora.com/job/1"]["duplicate_site"] == "linkedin"

    client.post("/api/profile", json=PROFILE)
    client.post("/api/settings", json={"EXECUTION_ENGINE": "local_playwright", "GEMINI_API_KEY": "k-12345678"})
    client.post("/api/apply", json={"url": "https://au.jora.com/job/1"})
    for _ in range(50):
        s = client.get("/api/apply").json()
        if s["state"] == "error":
            break
        time.sleep(0.1)
    assert "same role as a listing on linkedin" in s["message"]


def test_gemini_key_check(client, monkeypatch):
    import httpx
    assert client.post("/api/settings/test-gemini").status_code == 400  # no key yet
    client.post("/api/settings", json={"GEMINI_API_KEY": "AIza-bad-key-0000"})
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers))
        req = httpx.Request("POST", url)
        if headers["x-goog-api-key"] == "AIza-good-key-111":
            return httpx.Response(200, json={"candidates": []}, request=req)
        return httpx.Response(400, json=[{"error": {"message": "Please pass a valid API key"}}], request=req)

    monkeypatch.setattr(httpx, "post", fake_post)
    r = client.post("/api/settings/test-gemini")
    assert r.status_code == 400 and "Please pass a valid API key" in r.json()["detail"]
    assert calls[0][0].endswith("/models/gemini-3.5-flash-lite:generateContent")  # same default as the pipeline

    client.post("/api/settings", json={"GEMINI_API_KEY": "AIza-good-key-111", "LLM_MODEL": "gemini-3.5-flash"})
    assert client.post("/api/settings/test-gemini").json() == {"ok": True, "model": "gemini-3.5-flash"}


def test_job_list_links_to_listing_when_no_application_link(client, tmp_path):
    """Regression: a stored 'None' link made the job title open /None on the UI ({"detail":"Not Found"})."""
    _seed_job(tmp_path, "https://linkedin.com/jobs/view/1")
    database.get_connection().execute(
        "UPDATE jobs SET application_url = 'None' WHERE url = 'https://linkedin.com/jobs/view/1'")
    jobs = client.get("/api/jobs?view=all").json()
    assert jobs[0]["link"] == "https://linkedin.com/jobs/view/1"


def test_saved_answers_list_and_delete(client):
    from applypilot.apply.answers import save_answer
    conn = database.get_connection()
    save_answer(conn, "Notice period", "2 weeks", "user")
    save_answer(conn, "Expected salary", "110000", "ai")
    rows = client.get("/api/answers").json()
    assert [(r["question"], r["source"]) for r in rows] == [("Notice period", "user"), ("Expected salary", "ai")]
    client.post("/api/answers/delete", json={"question_key": "notice period"})
    assert [r["question"] for r in client.get("/api/answers").json()] == ["Expected salary"]


def test_status_shows_gemini_budget(client, monkeypatch):
    from applypilot import budget
    assert client.get("/api/status").json()["budget"] is None  # no Gemini key yet
    client.post("/api/settings", json={"GEMINI_API_KEY": "k-12345678", "GEMINI_DAILY_LIMIT": "200"})
    monkeypatch.setenv("GEMINI_API_KEY", "k-12345678")
    monkeypatch.setenv("GEMINI_DAILY_LIMIT", "200")
    budget.record(config.get_local_engine_model(), 20)
    b = client.get("/api/status").json()["budget"]
    assert (b["used"], b["limit"], b["left"], b["pipeline_left"]) == (20, 200, 180, 130)
    assert client.post("/api/settings", json={"GEMINI_DAILY_LIMIT": "lots"}).status_code == 400


def test_redo_marks_and_starts_rewriting(client, tmp_path, monkeypatch):
    _seed_job(tmp_path, "https://example.com/job/1")
    started = []
    from applypilot.ui import server
    monkeypatch.setattr(server.SubprocessTask, "start", lambda self, label, args, **kw: started.append(args))
    r = client.post("/api/jobs/redo", json={"url": "https://example.com/job/1"})
    assert r.status_code == 200 and r.json()["marked"] == 1
    assert started == [["redo", "--pending"]]  # only the marked jobs, whatever their score
    [job] = client.get("/api/jobs?view=ready").json()  # still ready: old documents kept until replaced
    assert job["redo_requested"] == 1
    # Try again / Redo use the Strictness picked on Find jobs
    client.post("/api/jobs/redo", json={"url": "https://example.com/job/1", "validation": "lenient"})
    assert started[-1] == ["redo", "--pending", "--validation", "lenient"]


def test_quick_view_and_locate(client, tmp_path, monkeypatch):
    _seed_job(tmp_path, "https://example.com/job/1")
    _seed_job(tmp_path, "https://example.com/job/2")
    conn = database.get_connection()
    conn.execute("UPDATE jobs SET apply_site = 'greenhouse', apply_site_name = 'Greenhouse', apply_account = 'no' "
                 "WHERE url LIKE '%/1'")
    conn.execute("UPDATE jobs SET apply_site = 'workday', apply_site_name = 'Workday', apply_account = 'yes' "
                 "WHERE url LIKE '%/2'")
    conn.commit()
    quick = client.get("/api/jobs?view=ready&quick=true").json()
    assert [j["url"] for j in quick] == ["https://example.com/job/1"]
    assert quick[0]["apply_site_name"] == "Greenhouse" and quick[0]["apply_account"] == "no"
    assert len(client.get("/api/jobs?view=ready").json()) == 2

    started = []
    from applypilot.ui import server
    monkeypatch.setattr(server.SubprocessTask, "start", lambda self, label, args, **kw: started.append((label, args)))
    assert client.post("/api/jobs/locate", json={"min_score": 8}).status_code == 200
    assert started == [("Check where to apply", ["locate", "--min-score", "8"])]


def test_profile_choices_are_drop_downs_and_old_wording_is_mapped(client):
    from applypilot import profile_options as po
    profile = {"personal": {"full_name": "Jane Doe", "email": "j@x.com", "phone": "0412 345 678",
                            "province_state": "New South Wales"},
               "work_authorization": {"legally_authorized_to_work": True, "require_sponsorship": "false",
                                      "work_permit_type": "citizen", "security_clearance": "None"},
               "experience": {"education_level": "Masters"},
               "eeo_voluntary": {"gender": "Decline to self-identify"}}
    assert client.post("/api/profile", json=profile).status_code == 200
    data = client.get("/api/profile").json()
    p = data["profile"]
    assert (p["personal"]["province_state"], p["work_authorization"]["legally_authorized_to_work"],
            p["work_authorization"]["require_sponsorship"], p["work_authorization"]["work_permit_type"],
            p["work_authorization"]["security_clearance"], p["experience"]["education_level"],
            p["eeo_voluntary"]["gender"]) == ("NSW", "Yes", "No", "Australian citizen", "", "Master's degree",
                                             "Prefer not to say")
    assert {o["label"] for o in data["options"]["work_authorization.security_clearance"]} >= {"None", "NV1"}
    assert "experience.years_of_experience_total" in data["numbers"]
    # Saved in the canonical wording, and a made-up value is refused
    saved = json.loads(config.PROFILE_PATH.read_text())
    assert saved["work_authorization"]["work_permit_type"] == "Australian citizen"
    bad = {**profile, "work_authorization": {"security_clearance": "Top secret-ish"}}
    r = client.post("/api/profile", json=bad)
    assert r.status_code == 400 and "security clearance" in r.json()["detail"]
    assert po.normalise("availability.notice_period", "Immediate") == "Immediately"


def test_skills_confirmation_round_trip(client):
    profile = {"personal": {"full_name": "Jane Doe", "email": "j@x.com", "phone": "0412 345 678"},
               "skills_boundary": {"tools": ["Git", "CI/CD"]}}
    client.post("/api/profile", json={**profile, "skills_confirmed": True})
    data = client.get("/api/profile").json()
    assert data["skills_confirmed"] is True and "skills_confirmed" not in data["profile"]
    assert json.loads(config.PROFILE_PATH.read_text())["skills_confirmed"] is True
    client.post("/api/profile", json=profile)  # unticked
    assert client.get("/api/profile").json()["skills_confirmed"] is False


def test_template_examples_are_never_saved_or_used(client):
    from applypilot import config
    leftover = {**PROFILE, "personal": {**PROFILE["personal"], "city": "Your City"},
                "resume_facts": {"preserved_companies": ["Company A"]}}
    r = client.post("/api/profile", json=leftover)
    assert r.status_code == 400 and "city" in r.json()["detail"] and "preserved companies" in r.json()["detail"]
    # A profile saved before this check: the examples are blanked when it's used
    config.PROFILE_PATH.write_text(json.dumps(leftover))
    used = config.load_profile()
    assert used["personal"]["city"] == "" and used["resume_facts"]["preserved_companies"] == []
    assert used["personal"]["full_name"] == PROFILE["personal"]["full_name"]


def test_mark_applied_for_jobs_applied_to_outside_the_app(client, tmp_path):
    _seed_job(tmp_path, "https://example.com/job/1")
    assert client.post("/api/jobs/mark-applied", json={"url": "https://example.com/nope"}).status_code == 404
    assert client.post("/api/jobs/mark-applied", json={"url": "https://example.com/job/1"}).json() == {"ok": True}
    assert client.get("/api/jobs?view=ready").json() == []
    assert [j["url"] for j in client.get("/api/jobs?view=applied").json()] == ["https://example.com/job/1"]

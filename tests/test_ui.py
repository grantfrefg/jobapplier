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

    s = client.post("/api/searches", json={"location": "Sydney, NSW", "distance": 0,
                                           "roles": ["Data Analyst", " "]}).json()
    assert s["simple"] == {"location": "Sydney, NSW", "distance": 0, "roles": ["Data Analyst"]}
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
    assert client.post("/api/apply", json={"url": url}).status_code == 409  # one at a time

    # Re-scan goes back to filling, then waits again
    client.post("/api/apply/decision", json={"decision": "rescan"})
    wait_for("waiting")
    assert len(prompts) == 3  # step 1, step 2, step 2 again

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

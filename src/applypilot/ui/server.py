"""Local web UI: setup, pipeline runs, and human-approved applying.

Run with ``applypilot ui`` and open http://localhost:8000. Everything the
terminal flow does is available here:

  * Setup     - .env keys and engine, resume, profile.json, searches.yaml
  * Find jobs - runs ``applypilot run <stages>`` as a subprocess, streams its log
  * Apply     - lists tailored jobs; with EXECUTION_ENGINE=local_playwright the
                local engine fills the form and the approval gate becomes
                buttons on this page instead of a terminal prompt.

The server is meant to listen on localhost only: it can read and write your
API keys and drive a browser.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from applypilot import budget, config

STATIC_DIR = Path(__file__).parent / "static"
PROFILE_TEMPLATE_PATH = Path(__file__).parent / "profile_template.json"

ENV_KEYS = ("EXECUTION_ENGINE", "GEMINI_API_KEY", "LLM_MODEL", "OPENAI_API_KEY", "CAPSOLVER_API_KEY",
            "GEMINI_DAILY_LIMIT", "GEMINI_APPLY_RESERVE")
NUMBER_KEYS = {"GEMINI_DAILY_LIMIT", "GEMINI_APPLY_RESERVE"}
SECRET_KEYS = {"GEMINI_API_KEY", "OPENAI_API_KEY", "CAPSOLVER_API_KEY"}
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")
GATE_DECISIONS = ("applied", "manual", "rescan", "declined")


# ---------------------------------------------------------------------------
# Background work: subprocess tasks and the local apply session
# ---------------------------------------------------------------------------

def applypilot_command(*args: str) -> list[str]:
    """Command line that runs the ApplyPilot CLI in a child process.

    A packaged desktop app has no `python -m`; its executable dispatches
    CLI arguments itself (see packaging/desktop_entry.py).
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "applypilot", *args]


def browser_install_command() -> tuple[list[str], dict]:
    """Playwright's own installer for Chromium (works inside a packaged app too)."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    node, cli = compute_driver_executable()
    return [node, cli, "install", "--no-shell", "chromium"], get_driver_env()


class SubprocessTask:
    """One child process (CLI run or browser install) with a rolling log.

    Output goes through a log file rather than a pipe: a windowed desktop
    app has no console, and the child re-opens the file itself via
    APPLYPILOT_CHILD_LOG (see applypilot/__main__.py).
    """

    def __init__(self) -> None:
        self.lines: deque[str] = deque(maxlen=3000)
        self.proc: subprocess.Popen | None = None
        self.label = ""
        self.returncode: int | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        # Running until the log pump has drained the output and set returncode,
        # so callers never see running=False with returncode=None.
        return self.proc is not None and self.returncode is None

    def start(self, label: str, args: list[str], cmd: list[str] | None = None,
              env: dict | None = None) -> None:
        with self._lock:
            if self.running:
                raise HTTPException(409, f"'{self.label}' is still running")
            env = dict(env or os.environ)
            log_path = config.LOG_DIR / "ui-task.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("", encoding="utf-8")
            env.update(NO_COLOR="1", TERM="dumb", COLUMNS="120", PYTHONUNBUFFERED="1",
                       PYTHONIOENCODING="utf-8", APPLYPILOT_CHILD_LOG=str(log_path))
            self.lines.clear()
            self.lines.append(f"$ applypilot {' '.join(args)}" if cmd is None else f"$ {label}")
            self.label, self.returncode, self.proc = label, None, None
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            with open(log_path, "a", encoding="utf-8") as out:
                self.proc = subprocess.Popen(
                    cmd or applypilot_command(*args),
                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                    env=env, creationflags=flags,
                )
            threading.Thread(target=self._pump, args=(self.proc, log_path), daemon=True).start()

    def _pump(self, proc: subprocess.Popen, log_path: Path) -> None:
        pending = ""
        with open(log_path, encoding="utf-8", errors="replace") as f:
            while True:
                chunk = f.read()
                if chunk:
                    pending += chunk.replace("\r\n", "\n").replace("\r", "\n")
                    *done, pending = pending.split("\n")
                    self.lines.extend(done)
                elif proc.poll() is not None:
                    break
                else:
                    time.sleep(0.2)
        if pending:
            self.lines.append(pending)
        self.returncode = proc.returncode
        self.lines.append(f"[finished with exit code {self.returncode}]")

    def stop(self) -> None:
        if self.running and self.proc.poll() is None:
            self.proc.terminate()

    def snapshot(self) -> dict:
        return {"label": self.label, "running": self.running,
                "returncode": self.returncode, "log": list(self.lines)[-400:]}


class ApplySession:
    """Runs the local engine for one job in a thread; the gate waits on the UI."""

    def __init__(self) -> None:
        self.state = "idle"   # idle | filling | waiting | done | error
        self.job: dict | None = None
        self.needs: list[str] = []
        self.result: str | None = None
        self.message = ""
        self._decisions: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url: str, min_score: int) -> None:
        if self.busy:
            raise HTTPException(409, "An application is already in progress")
        self.state, self.job, self.needs, self.result, self.message = "filling", None, [], None, ""
        self._decisions = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(url, min_score), daemon=True)
        self._thread.start()

    def _run(self, url: str, min_score: int) -> None:
        try:
            self._apply(url, min_score)
        except Exception as e:  # never leave the page spinning on "filling"
            self.state, self.message = "error", str(e)[:300]

    def _apply(self, url: str, min_score: int) -> None:
        from applypilot.apply import launcher, local_engine

        from applypilot.database import get_connection, mark_duplicates
        conn = get_connection()
        mark_duplicates(conn)
        dup = conn.execute(
            "SELECT c.site, c.applied_at FROM jobs j JOIN jobs c ON c.url = j.duplicate_of WHERE j.url = ?",
            (url,)).fetchone()
        if dup:
            self.state = "error"
            self.message = (f"This is the same role as a listing on {dup['site']}"
                            + (", which you've already applied to." if dup["applied_at"]
                               else ". Apply to that one instead."))
            return
        job = launcher.acquire_job(target_url=url, min_score=min_score, worker_id=0)
        if not job:
            self.state = "error"
            self.message = "That job isn't ready to apply (no tailored resume, already in progress, or manual ATS)."
            return
        self.job = {k: job.get(k) for k in ("url", "title", "site", "location", "fit_score", "application_url")}
        try:
            result, duration_ms = local_engine.run_job(job, gate=self._gate)
        except Exception as e:  # browser failed to launch, profile missing, ...
            launcher.release_lock(job["url"])
            self.state, self.message = "error", str(e)[:300]
            return
        launcher.record_result(job, result, duration_ms)
        self.state, self.result = "done", result

    def _gate(self, page, needs_attention: list[str], dry_run: bool, snapshot=None) -> str:
        """Same contract as local_engine._approval_gate, answered from the UI.

        `snapshot()` records the answers on the page for the answer bank; it's
        called every few seconds so your edits are kept even after Submit.
        """
        self.needs, self.state = needs_attention, "waiting"
        ticks = 0
        while True:
            try:
                decision = self._decisions.get(timeout=1)
                break
            except queue.Empty:
                if page.is_closed():  # user closed the browser window
                    decision = "manual"
                    break
                ticks += 1
                if snapshot and ticks % 3 == 0:
                    snapshot()
        self.state = "filling" if decision == "rescan" else "closing"
        return decision

    def decide(self, decision: str) -> None:
        if self.state != "waiting":
            raise HTTPException(409, "Nothing is waiting for your approval")
        # Leave "waiting" now, so a status poll right after can't see the old state
        self.state = "filling" if decision == "rescan" else "closing"
        self._decisions.put(decision)

    def snapshot(self) -> dict:
        from applypilot.apply.local_engine import staged_uploads
        files = [{"kind": "cover" if p.name.endswith("_Cover_Letter.pdf") else "resume", "name": p.name,
                  "path": str(p)} for p in staged_uploads()] if self.state == "waiting" else []
        return {"state": self.state, "busy": self.busy, "job": self.job, "needs_attention": self.needs,
                "result": self.result, "message": self.message, "files": files,
                "upload_dir": str(config.UPLOAD_DIR)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _budget_status() -> dict | None:
    """Today's Gemini usage, when a Gemini key is what's being used."""
    if not os.environ.get("GEMINI_API_KEY") or os.environ.get("LLM_URL"):
        return None
    try:
        return budget.status(config.get_local_engine_model())
    except Exception:  # the database isn't ready yet
        return None

def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _read_env_file() -> dict[str, str]:
    from dotenv import dotenv_values
    if not config.ENV_PATH.exists():
        return {}
    return {k: v or "" for k, v in dotenv_values(config.ENV_PATH).items()}


def _write_env(updates: dict[str, str]) -> None:
    """Merge updates into ~/.applypilot/.env and this process's environment."""
    from dotenv import set_key, unset_key
    config.ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.ENV_PATH.touch(exist_ok=True)
    for key, value in updates.items():
        if value:
            set_key(str(config.ENV_PATH), key, value, quote_mode="never")
            os.environ[key] = value
        else:
            unset_key(str(config.ENV_PATH), key, quote_mode="never")
            os.environ.pop(key, None)


def _job_file(url: str, kind: str) -> Path:
    from applypilot.database import get_connection
    column = {"resume": "tailored_resume_path", "cover": "cover_letter_path"}.get(kind)
    if not column:
        raise HTTPException(400, "kind must be resume or cover")
    row = get_connection().execute(f"SELECT {column} FROM jobs WHERE url = ?", (url,)).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "No file for that job")
    pdf = Path(row[0]).with_suffix(".pdf")
    if not pdf.exists():
        raise HTTPException(404, "PDF not generated yet (run the pdf stage)")
    return pdf


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    from applypilot.database import apply_link, get_connection, get_stats, init_db

    config.load_env()
    config.ensure_dirs()
    init_db()
    desktop = config.is_desktop_app()
    if desktop:
        # Friend mode: always the free, human-approved engine
        os.environ["EXECUTION_ENGINE"] = config.ENGINE_LOCAL_PLAYWRIGHT

    app = FastAPI(title="ApplyPilot", docs_url=None, redoc_url=None)
    task = SubprocessTask()
    session = ApplySession()
    login: dict = {"proc": None}  # the normal (non-automated) login window, if open

    def login_open() -> bool:
        proc = login["proc"]
        return proc is not None and proc.poll() is None

    def require_profile_free() -> None:
        """The login window and the automated browser share one profile."""
        if login_open():
            raise HTTPException(409, "Close the login window first (its X button saves your logins): "
                                     "it uses the same browser profile")
    app.state.task, app.state.session = task, session
    app.state.last_request = time.time()
    app.state.shutdown = None  # set by applypilot.desktop

    @app.middleware("http")
    async def _touch(request: Request, call_next):
        app.state.last_request = time.time()
        return await call_next(request)

    def busy() -> bool:
        return task.running or session.busy

    app.state.busy = busy

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # -- status ------------------------------------------------------------

    @app.get("/api/status")
    def status() -> dict:
        tier = config.get_tier()
        stats = get_stats()
        return {
            "app": "applypilot",
            "desktop": desktop,
            "engine": config.get_execution_engine(),
            "local_model": config.get_local_engine_model(),
            "tier": tier,
            "tier_label": config.TIER_LABELS.get(tier, ""),
            "checks": {
                "profile": config.PROFILE_PATH.exists(),
                "resume_txt": config.RESUME_PATH.exists(),
                "resume_pdf": config.RESUME_PDF_PATH.exists(),
                "searches": config.SEARCH_CONFIG_PATH.exists(),
                "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
                "llm_key": any(os.environ.get(k) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")),
                "playwright": config.has_playwright(),
                "claude_cli": shutil.which("claude") is not None,
                "browser": bool(config.find_system_browsers()) or config.has_playwright_chromium(),
            },
            "stats": {k: stats.get(k, 0) for k in
                      ("total", "scored", "tailored", "with_cover_letter", "applied", "ready_to_apply")},
            "budget": _budget_status(),
            "vnc_url": os.environ.get("APPLYPILOT_VNC_URL", ""),
            "data_dir": str(config.APP_DIR),
        }

    # -- settings (.env) ------------------------------------------------------

    @app.get("/api/settings")
    def get_settings() -> dict:
        file_values = _read_env_file()
        out: dict = {}
        for key in ENV_KEYS:
            value = file_values.get(key) or os.environ.get(key, "")
            if key in SECRET_KEYS:
                out[key] = {"set": bool(value), "hint": f"…{value[-4:]}" if len(value) > 8 else ""}
            else:
                out[key] = value
        out["EXECUTION_ENGINE"] = out["EXECUTION_ENGINE"] or config.ENGINE_CLAUDE_CODE
        return out

    @app.post("/api/settings")
    def save_settings(body: dict) -> dict:
        updates: dict[str, str] = {}
        for key in ENV_KEYS:
            if key not in body or (desktop and key == "EXECUTION_ENGINE"):
                continue
            value = str(body[key] or "").strip()
            if key in SECRET_KEYS and not value:
                continue  # blank secret field = keep the saved one
            if key in NUMBER_KEYS and value and not value.isdigit():
                raise HTTPException(400, f"{key} must be a whole number")
            if key == "EXECUTION_ENGINE" and value not in config.EXECUTION_ENGINES:
                raise HTTPException(400, f"EXECUTION_ENGINE must be one of {config.EXECUTION_ENGINES}")
            updates[key] = value
        for key in body.get("clear", []):
            if key in SECRET_KEYS:
                updates[key] = ""
        _write_env(updates)
        return get_settings()

    @app.post("/api/settings/test-gemini")
    def test_gemini() -> dict:
        """One tiny request with the saved key and the model the pipeline uses."""
        import httpx
        from applypilot.llm import error_detail
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise HTTPException(400, "No Gemini API key saved yet")
        model = os.environ.get("LLM_MODEL") or config.DEFAULT_GEMINI_MODEL
        try:
            resp = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={"contents": [{"parts": [{"text": "Reply with OK"}]}]},
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Couldn't reach Google: {e}") from e
        if not resp.is_success:
            raise HTTPException(400, f"Gemini said: {error_detail(resp) or resp.status_code} (model: {model})")
        budget.record(model)
        return {"ok": True, "model": model}

    # -- resume ------------------------------------------------------------

    @app.get("/api/resume")
    def get_resume() -> dict:
        text = config.RESUME_PATH.read_text(encoding="utf-8") if config.RESUME_PATH.exists() else ""
        return {"text": text, "has_pdf": config.RESUME_PDF_PATH.exists()}

    @app.post("/api/resume")
    async def save_resume(text: str = Form(""), pdf: UploadFile | None = File(None)) -> dict:
        if text.strip():
            config.RESUME_PATH.write_text(text.strip() + "\n", encoding="utf-8")
        if pdf is not None and pdf.filename:
            data = await pdf.read()
            if not data.startswith(b"%PDF"):
                raise HTTPException(400, "That file isn't a PDF")
            config.RESUME_PDF_PATH.write_bytes(data)
        return get_resume()

    # -- profile -----------------------------------------------------------

    @app.get("/api/profile")
    def get_profile() -> dict:
        template = json.loads(PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8"))
        saved = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8")) if config.PROFILE_PATH.exists() else None
        return {"exists": saved is not None, "profile": _deep_merge(template, saved or {}),
                "template": template}

    @app.post("/api/profile")
    def save_profile(body: dict) -> dict:
        personal = body.get("personal") or {}
        missing = [k for k in ("full_name", "email", "phone") if not str(personal.get(k, "")).strip()]
        if missing:
            raise HTTPException(400, f"Required: {', '.join(missing)}")
        config.PROFILE_PATH.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        return get_profile()

    # -- searches ----------------------------------------------------------

    @app.get("/api/searches")
    def get_searches() -> dict:
        if config.SEARCH_CONFIG_PATH.exists():
            import yaml
            text = config.SEARCH_CONFIG_PATH.read_text(encoding="utf-8")
            try:
                parsed = yaml.safe_load(text) or {}
                defaults = parsed.get("defaults") or {}
                locs = [loc for loc in parsed.get("locations") or [] if isinstance(loc, dict)]
                simple = {
                    "location": str(defaults.get("location", "")),
                    "country": str(parsed.get("country") or "australia").lower(),
                    "include_remote": any(loc.get("remote") for loc in locs),
                    "exclude_titles": [str(t) for t in parsed.get("exclude_titles") or []],
                    "roles": [str(q.get("query", "")) for q in parsed.get("queries") or [] if isinstance(q, dict)],
                }
            except (yaml.YAMLError, AttributeError, TypeError, ValueError):
                simple = None
            return {"exists": True, "yaml": text, "simple": simple}
        example = config.CONFIG_DIR / "searches.example.yaml"
        return {"exists": False, "yaml": example.read_text(encoding="utf-8") if example.exists() else ""}

    @app.post("/api/searches")
    def save_searches(body: dict) -> dict:
        import yaml
        if "yaml" in body:
            text = str(body["yaml"])
        else:
            from applypilot.wizard.init import build_search_yaml
            roles = [r.strip() for r in body.get("roles", []) if str(r).strip()]
            if not roles:
                raise HTTPException(400, "Add at least one job title")
            location = str(body.get("location") or "").strip()
            if not location:
                raise HTTPException(400, "Add a city or region, e.g. Sydney, NSW")
            excludes = body.get("exclude_titles") or []
            if isinstance(excludes, str):
                excludes = excludes.split(",")
            text = build_search_yaml(location, roles, str(body.get("country") or "australia"),
                                     bool(body.get("include_remote")), [str(t) for t in excludes])
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(400, f"Invalid YAML: {e}") from e
        if not isinstance(parsed, dict) or not parsed.get("queries"):
            raise HTTPException(400, "searches.yaml needs a 'queries' list")
        config.SEARCH_CONFIG_PATH.write_text(text, encoding="utf-8")
        return get_searches()

    # -- pipeline (subprocess) ---------------------------------------------

    @app.post("/api/pipeline")
    def run_pipeline(body: dict) -> dict:
        stages = [s for s in body.get("stages", []) if s in VALID_STAGES] or ["all"]
        args = ["run", *stages, "--min-score", str(int(body.get("min_score", 7)))]
        if body.get("validation") in ("strict", "normal", "lenient"):
            args += ["--validation", body["validation"]]
        task.start("Find jobs: " + ", ".join(stages), args)
        return task.snapshot()

    @app.get("/api/pipeline")
    def pipeline_state() -> dict:
        return task.snapshot()

    @app.post("/api/pipeline/stop")
    def stop_pipeline() -> dict:
        task.stop()
        return task.snapshot()

    # -- jobs --------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs(view: str = "ready", quick: bool = False) -> list[dict]:
        from applypilot.database import mark_duplicates
        mark_duplicates(get_connection())
        where = {
            "ready": "tailored_resume_path IS NOT NULL AND applied_at IS NULL AND duplicate_of IS NULL "
                     "AND COALESCE(apply_status, '') NOT IN ('applied', 'in_progress')",
            "applied": "applied_at IS NOT NULL",
            "all": "1 = 1",
        }.get(view)
        if view == "ready" and quick:
            where += " AND apply_account IN ('no', 'board')"
        if where is None:
            raise HTTPException(400, "view must be ready, applied or all")
        rows = get_connection().execute(f"""
            SELECT url, title, company, site, location, fit_score, application_url, apply_status,
                   apply_error, applied_at, duplicate_of, redo_requested,
                   apply_site, apply_site_name, apply_account, apply_site_url,
                   tailored_resume_path IS NOT NULL AS tailored,
                   cover_letter_path IS NOT NULL AS has_cover,
                   (SELECT site FROM jobs c WHERE c.url = jobs.duplicate_of) AS duplicate_site,
                   (SELECT group_concat(DISTINCT d.site) FROM jobs d WHERE d.duplicate_of = jobs.url) AS also_on
            FROM jobs WHERE {where}
            ORDER BY fit_score IS NULL, fit_score DESC, discovered_at DESC
            LIMIT 500
        """).fetchall()
        return [{**dict(r), "link": apply_link(r)} for r in rows]

    @app.post("/api/jobs/locate")
    def locate_jobs(body: dict) -> dict:
        """Check where each ready job applies (opens the visible browser; no AI)."""
        if session.busy:
            raise HTTPException(409, "Finish the application that's open first (they share the browser)")
        require_profile_free()
        args = ["locate", "--min-score", str(int(body.get("min_score", 7)))]
        if body.get("recheck"):
            args.append("--recheck")
        task.start("Check where to apply", args)
        return task.snapshot()

    @app.post("/api/jobs/reset")
    def reset_job(body: dict) -> dict:
        conn = get_connection()
        conn.execute("""UPDATE jobs SET apply_status = NULL, apply_error = NULL, apply_attempts = 0,
                        agent_id = NULL WHERE url = ? AND applied_at IS NULL""", (body.get("url"),))
        conn.commit()
        return {"ok": True}

    @app.post("/api/jobs/redo")
    def redo_jobs(body: dict) -> dict:
        """Rewrite the resume and cover letter for one job (url) or all jobs not yet applied to.

        Only the marked jobs are rewritten, whatever their score, and each
        keeps its old documents until the new ones pass the checks.
        """
        from applypilot.database import redo_documents
        if task.running:
            raise HTTPException(409, f"'{task.label}' is still running; try again when it finishes")
        applying_now = (session.job or {}).get("url") if session.busy else None
        if applying_now and body.get("url") == applying_now:
            raise HTTPException(409, "You're applying to this job right now; finish that first")
        marked = redo_documents(get_connection(), body.get("url") or None, applying_now=applying_now)
        if marked:
            task.start("Redo resumes and cover letters", ["redo", "--pending"])
        return {"cleared": marked, "marked": marked, **task.snapshot()}

    @app.get("/api/jobs/file")
    def job_file(url: str, kind: str = "resume") -> FileResponse:
        return FileResponse(_job_file(url, kind), media_type="application/pdf")

    # -- answer bank ---------------------------------------------------------

    @app.get("/api/answers")
    def list_answers() -> list[dict]:
        from applypilot.apply.answers import ensure_table
        conn = get_connection()
        ensure_table(conn)
        rows = conn.execute("SELECT question_key, question, answer, source, uses, updated_at FROM answers "
                            "ORDER BY source = 'user' DESC, question").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/answers/delete")
    def delete_answer(body: dict) -> dict:
        conn = get_connection()
        conn.execute("DELETE FROM answers WHERE question_key = ?", (body.get("question_key"),))
        conn.commit()
        return {"ok": True}

    # -- browser -----------------------------------------------------------

    @app.get("/api/browser")
    def browser_state() -> dict:
        return {"system": config.find_system_browsers(),
                "playwright_chromium": config.has_playwright_chromium(),
                "login_open": login_open(),
                "install": task.snapshot() if task.label == "Install browser" else None}

    @app.post("/api/browser/install")
    def install_browser() -> dict:
        cmd, env = browser_install_command()
        task.start("Install browser", [], cmd=cmd, env=env)
        return browser_state()

    @app.post("/api/browser/login")
    def open_login(body: dict | None = None) -> dict:
        """Open the app's browser profile as a normal window to log in to Google, LinkedIn, SEEK..."""
        from applypilot.apply.login_browser import open_login_browser
        if session.busy:
            raise HTTPException(409, "Finish the current application first (they share the browser)")
        if task.running and task.label == "Check where to apply":
            raise HTTPException(409, "Wait for 'Check where to apply' to finish (they share the browser)")
        if login_open():
            return browser_state()
        url = str((body or {}).get("url") or "").strip()
        try:
            login["proc"] = open_login_browser([url] if url.startswith(("http://", "https://")) else None)
        except Exception as e:
            raise HTTPException(500, str(e)[:300]) from e
        return browser_state()

    @app.post("/api/browser/login/close")
    def close_login() -> dict:
        """Fallback when the user can't close it: logins from the last ~30 s may not be saved
        (the browser writes cookies to disk periodically and when its window is closed)."""
        proc = login["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        login["proc"] = None
        return browser_state()

    @app.post("/api/browser/test")
    def test_browser() -> dict:
        """Open the browser for a few seconds to prove automation works."""
        if session.busy:
            raise HTTPException(409, "Finish the current application first")
        require_profile_free()
        from playwright.sync_api import sync_playwright
        from applypilot.apply.local_engine import launch_browser
        try:
            with sync_playwright() as p:
                context = launch_browser(p)
                page = context.pages[0] if context.pages else context.new_page()
                page.set_content(
                    "<body style='font:24px system-ui;display:grid;place-items:center;height:90vh'>"
                    "<div>&#10003; ApplyPilot can control this browser.<br>"
                    "<small>This window closes by itself.</small></div></body>")
                page.wait_for_timeout(3000)
                context.close()
        except Exception as e:
            raise HTTPException(500, str(e)[:600]) from e
        return {"ok": True}

    # -- app lifecycle -----------------------------------------------------

    @app.post("/api/shutdown")
    def shutdown() -> dict:
        if app.state.shutdown is None:
            raise HTTPException(404, "Quit is only available in the desktop app")
        task.stop()
        app.state.shutdown()
        return {"ok": True}

    # -- apply -------------------------------------------------------------

    @app.post("/api/apply")
    def start_apply(body: dict) -> dict:
        url = str(body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "Pick a job to apply to")
        if not config.PROFILE_PATH.exists():
            raise HTTPException(400, "Save your profile first (Setup tab)")
        if config.get_execution_engine() == config.ENGINE_LOCAL_PLAYWRIGHT:
            if not os.environ.get("GEMINI_API_KEY"):
                raise HTTPException(400, "Add your Gemini API key first (Setup tab)")
            if task.running and task.label == "Check where to apply":
                raise HTTPException(409, "Wait for 'Check where to apply' to finish (they share the browser)")
            require_profile_free()
            session.start(url, int(body.get("min_score", 0)))
            return {"mode": "local", **session.snapshot()}
        # Claude Code engine is autonomous: run the normal CLI as a subprocess
        task.start(f"Apply (Claude Code): {url}", ["apply", "--url", url])
        return {"mode": "claude_code", **task.snapshot()}

    @app.get("/api/apply")
    def apply_state() -> dict:
        return session.snapshot()

    @app.post("/api/apply/decision")
    def apply_decision(body: dict) -> dict:
        decision = body.get("decision")
        if decision not in GATE_DECISIONS:
            raise HTTPException(400, f"decision must be one of {GATE_DECISIONS}")
        session.decide(decision)
        return session.snapshot()

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")

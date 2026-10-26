"""Tests for the EXECUTION_ENGINE toggle and the local Playwright engine."""

import json
import os
import re

import pytest

from applypilot import config
from applypilot.apply import launcher, local_engine as le


PROFILE = {
    "personal": {
        "full_name": "Jane Quinn Doe",
        "preferred_name": "Jane",
        "email": "jane.doe@example.com",
        "password": "hunter2-secret",
        "phone": "+61 412 345 678",
        "address": "42 Wallaby Way",
        "city": "Sydney",
        "province_state": "NSW",
        "country": "Australia",
        "postal_code": "2000",
        "linkedin_url": "https://www.linkedin.com/in/janedoe",
    },
    "work_authorization": {
        "legally_authorized_to_work": "Yes",
        "require_sponsorship": "No",
        "work_permit_type": "Australian citizen",
        "tax_file_number": "123 456 782",
        "drivers_licence": "Yes",
    },
    "compensation": {"salary_expectation": "120000", "salary_currency": "AUD"},
}

RESUME = (
    "Jane Quinn Doe\njane.doe@example.com | +61 412 345 678 | 42 Wallaby Way, Sydney 2000\n"
    "Senior Engineer, Acme 2019 - 2023. Will lead projects. Python, AWS.\n"
)


# ---------------------------------------------------------------------------
# Config toggle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (None, "claude_code"),
    ("", "claude_code"),
    ("claude_code", "claude_code"),
    ("local_playwright", "local_playwright"),
    ("  LOCAL_PLAYWRIGHT ", "local_playwright"),
    ("bogus", "claude_code"),
])
def test_execution_engine(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("EXECUTION_ENGINE", raising=False)
    else:
        monkeypatch.setenv("EXECUTION_ENGINE", raw)
    assert config.get_execution_engine() == expected


@pytest.mark.parametrize("raw, expected", [
    (None, "gemini-3.5-flash-lite"),
    ("gemini-1.5-flash", "gemini-1.5-flash"),
    ("gpt-4o-mini", "gemini-3.5-flash-lite"),  # non-Gemini model for other stages is ignored
])
def test_local_engine_model(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("LLM_MODEL", raw)
    assert config.get_local_engine_model() == expected


def test_run_job_default_uses_claude_code(monkeypatch):
    monkeypatch.delenv("EXECUTION_ENGINE", raising=False)

    class ClaudePath(Exception):
        pass

    def fake_build_prompt(**kwargs):
        raise ClaudePath

    monkeypatch.setattr(launcher.prompt_mod, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(le, "run_job", lambda *a, **k: pytest.fail("local engine used"))
    with pytest.raises(ClaudePath):
        launcher.run_job({"title": "t", "url": "u"}, port=9222)


def test_run_job_local_dispatch(monkeypatch):
    monkeypatch.setenv("EXECUTION_ENGINE", "local_playwright")
    calls = []
    monkeypatch.setattr(le, "run_job", lambda job, worker_id, dry_run: calls.append(job) or ("manual", 5))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: pytest.fail("claude spawned"))
    assert launcher.run_job({"title": "t", "url": "u"}, port=9222) == ("manual", 5)
    assert calls


# ---------------------------------------------------------------------------
# Safety gate classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Submit application", "submit"),
    ("Submit", "submit"),
    ("Apply", "submit"),
    ("Apply now", "submit"),
    ("Quick apply", "submit"),
    ("Send Application", "submit"),
    ("Continue to apply", "submit"),  # deny wins over allow
    ("Finish", "submit"),
    ("Next", "next"),
    ("Continue", "next"),
    ("Save and Continue", "next"),
    ("Back", None),
    ("", None),
])
def test_classify_control(text, expected):
    assert le.classify_control(text) == expected


# ---------------------------------------------------------------------------
# Privacy redaction
# ---------------------------------------------------------------------------

SECRETS = ["Jane", "Doe", "jane.doe@example.com", "412 345 678", "Wallaby", "2000",
           "123 456 782", "hunter2", "linkedin.com/in/janedoe"]


def _leaks(secret: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(secret)}(?!\w)", text) is not None


def test_redaction_keeps_pii_local():
    ph = le.build_placeholders(PROFILE)
    safe_profile = json.dumps(le.redacted_profile(PROFILE, ph))
    safe_resume = le.redact(RESUME, ph)
    for secret in SECRETS:
        assert not _leaks(secret, safe_profile), secret
        assert not _leaks(secret, safe_resume), secret
    # Non-identifying content survives
    assert "2019 - 2023" in safe_resume
    assert "Will lead projects" in safe_resume
    assert "Sydney" in safe_profile and "Australian citizen" in safe_profile
    assert '"drivers_licence": "Yes"' in safe_profile
    # Tax ID token exists and round-trips locally
    tfn_token = next(t for t in ph if "TAX_FILE_NUMBER" in t)
    assert le.unredact(tfn_token, ph) == "123 456 782"


def test_unredact():
    ph = le.build_placeholders(PROFILE)
    assert le.unredact("{{FIRST_NAME}} {{LAST_NAME}}", ph) == "Jane Doe"
    assert le.unredact("{{UNKNOWN_TOKEN}}", ph) is None
    assert le.unredact("plain", ph) == "plain"


def test_parse_json_response():
    assert le.parse_json_response('```json\n{"actions": [1]}\n```')["actions"] == [1]
    assert le.parse_json_response('Sure! {"actions": []} done')["needs_attention"] == []
    assert le.parse_json_response('[{"id": "a"}]')["actions"] == [{"id": "a"}]


@pytest.mark.parametrize("url, ats", [
    ("https://www.seek.com.au/job/123/apply", "seek"),
    ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse"),
    ("https://jobs.lever.co/acme/abc", "lever"),
    ("https://acme.wd5.myworkdayjobs.com/x", "workday"),
    ("https://example.com/careers", None),
])
def test_detect_ats(url, ats):
    assert le.detect_ats(url) == ats


# ---------------------------------------------------------------------------
# End-to-end in a real (visible) browser against a local multi-step form
# ---------------------------------------------------------------------------

FORM_HTML = """<!doctype html><html><body>
<form id="f" onsubmit="window.submitted = true; return false;">
 <div id="step1">
  <label for="fn">First name</label><input id="fn" required>
  <label for="ln">Last name</label><input id="ln" required>
  <label for="em">Email address</label><input id="em" type="email">
  <button type="button" id="cont"
    onclick="document.getElementById('step1').style.display='none';
             document.getElementById('step2').style.display='block'">Continue</button>
 </div>
 <div id="step2" style="display:none">
  <label for="rw">Which statement best describes your right to work in Australia?</label>
  <select id="rw"><option></option><option>Australian citizen</option>
   <option>I require sponsorship</option></select>
  <label for="cv">Resume</label><input id="cv" type="file">
  <label for="cl">Cover letter</label><textarea id="cl"></textarea>
  <button type="submit" id="sub" onclick="window.submitted = true">Submit application</button>
 </div>
</form></body></html>"""


def _fake_gemini(prompts):
    def call(prompt, api_key, model):
        prompts.append(prompt)
        fields = json.loads(prompt.split("FORM FIELDS:\n", 1)[1])
        by_label = {
            "First name": ("fill", "{{FIRST_NAME}}"),
            "Last name": ("fill", "{{LAST_NAME}}"),
            "Email address": ("fill", "{{EMAIL}}"),
            "Resume": ("upload", "{{RESUME_PDF}}"),
            "Cover letter": ("fill", "{{COVER_LETTER_TEXT}}"),
        }
        actions = []
        for f in fields:
            if f["label"] in by_label:
                act, val = by_label[f["label"]]
                actions.append({"id": f["id"], "action": act, "value": val})
            elif "right to work" in f["label"]:
                actions.append({"id": f["id"], "action": "select", "value": "Australian citizen"})
        return {"actions": actions, "needs_attention": []}
    return call


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")
def test_local_engine_end_to_end(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    form = tmp_path / "form.html"
    form.write_text(FORM_HTML)
    (tmp_path / "tailored").mkdir()
    resume_txt = tmp_path / "tailored" / "acme.txt"
    resume_txt.write_text(RESUME)
    resume_txt.with_suffix(".pdf").write_bytes(b"%PDF-1.4 fake")
    cl = tmp_path / "tailored" / "acme_cl.txt"
    cl.write_text("Dear Hiring Manager, I am Jane Quinn Doe.")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(PROFILE))
    monkeypatch.setattr(config, "PROFILE_PATH", profile_path)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(config, "APPLY_WORKER_DIR", tmp_path / "apply")
    monkeypatch.setattr(config, "LOCAL_BROWSER_PROFILE_DIR", tmp_path / "browser")

    prompts: list[str] = []
    monkeypatch.setattr(le, "_call_gemini", _fake_gemini(prompts))

    seen = {}

    def fake_gate(page, needs, dry_run):
        seen.update(page.evaluate("""() => ({
            fn: fn.value, ln: ln.value, em: em.value, rw: rw.value,
            cv: cv.files.length ? cv.files[0].name : '', cl: cl.value,
            submitted: !!window.submitted,
            outlined: getComputedStyle(sub).outlineStyle,
        })"""))
        return "manual"

    job = {"title": "Engineer", "site": "Acme", "url": form.as_uri(), "application_url": None,
           "tailored_resume_path": str(resume_txt), "cover_letter_path": str(cl), "fit_score": 9}
    status, _ = le.run_job(job, gate=fake_gate)

    assert status == "manual"
    assert seen["fn"] == "Jane" and seen["ln"] == "Doe"
    assert seen["em"] == "jane.doe@example.com"
    assert seen["rw"] == "Australian citizen"
    assert seen["cv"] == "Jane_Quinn_Doe_Resume.pdf"
    assert "Jane Quinn Doe" in seen["cl"]  # real text swapped in locally
    assert seen["submitted"] is False      # automation never clicked Submit
    assert seen["outlined"] == "solid"     # Submit highlighted for the human
    assert len(prompts) == 1               # step 1 is all standard fields: no AI request
    for p in prompts:
        for secret in SECRETS:
            assert not _leaks(secret, p), secret


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")
def test_approval_gate_blocks_on_terminal(tmp_path, monkeypatch):
    """The real gate highlights Submit and waits on input(); 'y' records applied."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    answers = iter(["", "y"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(le.sys.stdin, "isatty", lambda: True, raising=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_content('<button id="s">Submit application</button><button>Next</button>')
        _, controls, frame_by_id = le._scan(page)
        le._highlight_submit(controls, frame_by_id)
        assert page.evaluate("getComputedStyle(s).outlineStyle") == "solid"
        assert le._approval_gate(page, [], False) == "applied"
        browser.close()


def test_gemini_error_message_is_readable(monkeypatch):
    import httpx

    def handler(request):
        assert request.headers["x-goog-api-key"] == "bad"
        return httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}})

    real_client = httpx.Client
    monkeypatch.setattr(le.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(RuntimeError, match="API key not valid"):
        le._call_gemini("hi", "bad", "gemini-3.5-flash-lite")


def test_pipeline_llm_error_includes_google_message():
    """The pipeline's LLM client must say *why* Gemini refused, not just '400'."""
    import httpx
    from applypilot import llm

    client = llm.LLMClient(llm._GEMINI_COMPAT_BASE, llm.DEFAULT_GEMINI_MODEL, "bad")
    client._client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(400, json=[{"error": {"message": "Please pass a valid API key"}}])))
    with pytest.raises(httpx.HTTPStatusError, match="Please pass a valid API key"):
        client.ask("hi")
    assert llm.DEFAULT_GEMINI_MODEL == config.DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"


def test_env_example_values_are_really_empty():
    """'KEY=   # comment' parses as the comment text, which then gets sent as an API key."""
    from pathlib import Path
    from dotenv import dotenv_values
    example = Path(__file__).resolve().parent.parent / ".env.example"
    values = dotenv_values(example)
    assert values.pop("EXECUTION_ENGINE") == "claude_code"
    assert values == {"GEMINI_API_KEY": "", "CAPSOLVER_API_KEY": ""}

    # Uncommenting an optional "# KEY=" line must not pick up a note either
    import io
    import re
    uncommented = re.sub(r"^# ([A-Z_]+=)", r"\1", example.read_text(), flags=re.M)
    for key, value in dotenv_values(stream=io.StringIO(uncommented)).items():
        assert "#" not in (value or ""), f"{key} would be set to a comment: {value!r}"


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")
def test_unreachable_job_page_is_reported_not_blank(tmp_path, monkeypatch):
    """Regression: a failed page load left a blank browser with no explanation."""
    pytest.importorskip("playwright")
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    (tailored / "a.txt").write_text(RESUME)
    (tailored / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(PROFILE))
    for name, value in {"PROFILE_PATH": profile_path, "LOG_DIR": tmp_path, "APPLY_WORKER_DIR": tmp_path / "apply",
                        "LOCAL_BROWSER_PROFILE_DIR": tmp_path / "browser"}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(le, "_call_gemini", _fake_gemini([]))

    seen = {}

    def gate(page, needs, dry_run):
        seen["needs"] = needs
        return "manual"

    job = {"title": "Engineer", "site": "Acme", "url": "http://127.0.0.1:9/job", "application_url": "None",
           "tailored_resume_path": str(tailored / "a.txt"), "cover_letter_path": None, "fit_score": 9}
    status, _ = le.run_job(job, gate=gate)
    assert status == "manual"
    assert "Couldn't open the job page (http://127.0.0.1:9/job)" in seen["needs"][0]

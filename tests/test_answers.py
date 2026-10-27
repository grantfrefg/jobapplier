"""Standard fields and the answer bank: answers that need no AI request."""

import json
import os
import sqlite3

import pytest

from applypilot import config, database
from applypilot.apply import answers
from applypilot.apply import local_engine as le

LOCAL = {"{{FIRST_NAME}}": "Jane", "{{LAST_NAME}}": "Doe", "{{EMAIL}}": "jane@example.com",
         "{{PHONE}}": "0412 345 678", "{{LINKEDIN_URL}}": "https://linkedin.com/in/jane"}
FILES = {"{{RESUME_PDF}}": "/tmp/r.pdf"}
PROFILE = {"personal": {"city": "Sydney", "province_state": "NSW", "country": "Australia"}}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    answers.ensure_table(c)
    return c


def test_standard_fields_filled_in_code():
    fields = [
        {"id": "a", "type": "text", "label": "First name *"},
        {"id": "b", "type": "text", "label": "Surname"},
        {"id": "c", "type": "text", "label": "Contact", "name": "email"},
        {"id": "d", "type": "tel", "label": "Mobile number"},
        {"id": "e", "type": "select", "label": "State", "options": ["Select...", "New South Wales", "Victoria"]},
        {"id": "f", "type": "file", "label": "Upload your CV"},
        {"id": "g", "type": "text", "label": "LinkedIn profile"},
        {"id": "h", "type": "text", "label": "Years of SQL experience"},
    ]
    actions, remaining = answers.standard_actions(fields, LOCAL, FILES, PROFILE)
    got = {a["id"]: a["value"] for a in actions}
    assert got == {"a": "{{FIRST_NAME}}", "b": "{{LAST_NAME}}", "c": "{{EMAIL}}", "d": "{{PHONE}}",
                   "e": "New South Wales", "f": "{{RESUME_PDF}}", "g": "{{LINKEDIN_URL}}"}
    assert [f["id"] for f in remaining] == ["h"]


def test_prefilled_and_answered_fields_are_left_alone():
    fields = [
        {"id": "a", "type": "text", "label": "First name", "value": "Janey"},
        {"id": "b", "type": "text", "label": "Why us?", "value": "Because"},
        {"id": "r1", "type": "radio", "label": "Yes", "group": "Over 18?", "checked": True},
        {"id": "r2", "type": "radio", "label": "No", "group": "Over 18?", "checked": False},
        {"id": "f", "type": "file", "label": "Resume", "has_file": True},
    ]
    actions, remaining = answers.standard_actions(fields, LOCAL, FILES, PROFILE)
    assert actions == [] and remaining == []


def test_long_label_is_a_question_not_a_contact_field():
    q = {"id": "a", "type": "text", "label": "Please tell us the name of the manager who referred you to this role"}
    _, remaining = answers.standard_actions([q], LOCAL, FILES, PROFILE)
    assert remaining == [q]


def test_ai_never_overwrites_your_answer(conn):
    assert answers.save_answer(conn, "Do you have a driver's licence?", "Yes", "user")
    assert not answers.save_answer(conn, "Do you have a drivers licence", "No", "ai")
    assert answers.lookup(conn, "do you have a drivers licence *") == "Yes"
    assert answers.save_answer(conn, "Do you have a driver's licence?", "No", "user")
    assert answers.lookup(conn, "Do you have a driver's licence?") == "No"


def test_bank_reuses_select_text_and_radio(conn):
    answers.save_answer(conn, "Right to work in Australia", "australian citizen", "user")
    answers.save_answer(conn, "Expected salary", "110000", "ai")
    answers.save_answer(conn, "Are you willing to relocate?", "No", "user")
    fields = [
        {"id": "s", "type": "select", "label": "Right to work in Australia *",
         "options": ["", "Australian citizen", "Visa holder"]},
        {"id": "t", "type": "text", "label": "Expected salary"},
        {"id": "y", "type": "radio", "label": "Yes", "group": "Are you willing to relocate?"},
        {"id": "n", "type": "radio", "label": "No", "group": "Are you willing to relocate?"},
        {"id": "u", "type": "text", "label": "Notice period"},
    ]
    actions, remaining, used = answers.bank_actions(conn, fields)
    assert {(a["id"], a["action"], a["value"]) for a in actions} == {
        ("s", "select", "Australian citizen"), ("t", "fill", "110000"), ("n", "check", "")}
    assert [f["id"] for f in remaining] == ["u"]
    assert len(used) == 3
    assert conn.execute("SELECT SUM(uses) FROM answers").fetchone()[0] == 3


def test_textarea_answers_are_never_reused(conn):
    answers.save_answer(conn, "Why do you want this job?", "Because", "user")
    field = {"id": "w", "type": "textarea", "label": "Why do you want this job?"}
    _, remaining, _ = answers.bank_actions(conn, [field])
    assert remaining == [field]
    assert answers.answers_on_page([{**field, "value": "Because"}], set()) == {}


def test_answers_on_page_skips_personal_details():
    fields = [
        {"id": "a", "type": "text", "label": "First name", "value": "Jane"},
        {"id": "b", "type": "text", "label": "Referee", "value": "jane@example.com"},
        {"id": "c", "type": "select", "label": "Notice period", "value": "2 weeks"},
        {"id": "d", "type": "select", "label": "Gender", "value": "Select..."},
        {"id": "r", "type": "radio", "label": "Yes", "group": "Over 18?", "checked": True},
    ]
    found = answers.answers_on_page(fields, {"jane", "jane@example.com"})
    assert found == {"notice period": ("Notice period", "2 weeks"), "over 18": ("Over 18?", "Yes")}


# ---------------------------------------------------------------------------
# In a real browser: known pages cost no Gemini request; your edits are learned
# ---------------------------------------------------------------------------

FORM = """<!doctype html><html><body><form onsubmit="return false">
<label for="fn">First name</label><input id="fn">
<label for="ln">Last name</label><input id="ln">
<label for="em">Email</label><input id="em" type="email">
<label for="rw">Do you have the right to work in Australia?</label>
<select id="rw"><option></option><option>Yes</option><option>No</option></select>
<label for="np">Notice period</label><input id="np">
<label for="cv">Resume</label><input id="cv" type="file">
<button type="submit">Submit application</button>
</form></body></html>"""


def _setup(tmp_path, monkeypatch):
    tailored = tmp_path / "tailored"
    tailored.mkdir()
    (tailored / "a.txt").write_text("Jane Doe\nAnalyst")
    (tailored / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    profile = {"personal": {"full_name": "Jane Doe", "email": "jane@example.com", "phone": "0412 345 678"}}
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    for name, value in {"PROFILE_PATH": tmp_path / "profile.json", "LOG_DIR": tmp_path,
                        "APPLY_WORKER_DIR": tmp_path / "apply",
                        "LOCAL_BROWSER_PROFILE_DIR": tmp_path / "browser"}.items():
        monkeypatch.setattr(config, name, value)
    form = tmp_path / "form.html"
    form.write_text(FORM)
    return {"title": "Analyst", "site": "Acme", "url": form.as_uri(), "application_url": None,
            "tailored_resume_path": str(tailored / "a.txt"), "cover_letter_path": None, "fit_score": 9}


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")
def test_known_page_needs_no_ai_and_your_edits_are_learned(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    job = _setup(tmp_path, monkeypatch)
    prompts = []

    def fake_gemini(prompt, api_key, model):
        prompts.append(prompt)
        fields = json.loads(prompt.split("FORM FIELDS:\n", 1)[1])
        assert {f["label"] for f in fields} == {"Do you have the right to work in Australia?", "Notice period"}
        acts = [{"id": f["id"], "action": "select", "value": "Yes"} for f in fields if f["type"] == "select"]
        return {"actions": acts, "needs_attention": ["Notice period"]}

    monkeypatch.setattr(le, "_call_ai", fake_gemini)

    def gate(page, needs, dry_run, snapshot=None):
        page.fill("#np", "4 weeks")   # you answer what the AI couldn't
        snapshot()
        page.evaluate("document.querySelector('form').remove()")  # Submit navigated away
        return "applied"

    assert le.run_job(job, gate=gate)[0] == "applied"
    assert len(prompts) == 1
    conn = database.get_connection()
    rows = dict(conn.execute("SELECT question_key, answer || '/' || source FROM answers").fetchall())
    assert rows == {"do you have the right to work in australia": "Yes/ai", "notice period": "4 weeks/user"}

    # Second application with the same questions: zero Gemini requests
    seen = {}

    def gate2(page, needs, dry_run):
        seen.update(page.evaluate("() => ({fn: fn.value, em: em.value, rw: rw.value, np: np.value,"
                                  " cv: cv.files.length})"))
        return "manual"

    assert le.run_job(job, gate=gate2)[0] == "manual"
    assert len(prompts) == 1
    assert seen == {"fn": "Jane", "em": "jane@example.com", "rw": "Yes", "np": "4 weeks", "cv": 1}
    # Personal details never go into the bank
    assert not conn.execute("SELECT 1 FROM answers WHERE answer LIKE '%jane%'").fetchone()

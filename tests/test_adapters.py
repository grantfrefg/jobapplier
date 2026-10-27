"""Site adapters and the form patterns of Greenhouse, Lever and SEEK (E2).

The fixtures copy the markup of the real forms (checked against live
Greenhouse and Lever job pages): React-Select drop-downs with hidden
"required" twins, Lever's question cards and location autocomplete, SEEK's
document-choice radios that reveal the upload field.
"""

import json
import os

import pytest

from applypilot import config
from applypilot.apply import adapters
from applypilot.apply import local_engine as le

headed = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="headed browser needs a display (use xvfb-run)")

PROFILE = {"personal": {"full_name": "Jane Doe", "preferred_name": "Jane", "email": "jane@example.com",
                        "phone": "0412 345 678", "city": "Sydney", "province_state": "NSW", "country": "Australia",
                        "linkedin_url": "https://linkedin.com/in/janedoe"},
           "work_authorization": {"legally_authorized_to_work": "Yes", "require_sponsorship": "No"},
           "experience": {"current_company": "Acme", "target_role": "Backend Engineer"}}

# A searchable drop-down that behaves like React-Select: options appear on
# focus/typing as [role=option]; picking one shows it in a "single-value" div.
COMBO_JS = """
<script>
function combo(id, options) {
  const input = document.getElementById(id), box = input.closest('.select__container');
  const menu = box.querySelector('[role=listbox]'), chosen = box.querySelector('.select__single-value');
  const show = () => {
    const q = input.value.toLowerCase();
    menu.innerHTML = '';
    for (const o of options.filter(o => o.toLowerCase().includes(q))) {
      const d = document.createElement('div'); d.setAttribute('role', 'option'); d.textContent = o;
      d.onclick = () => { chosen.textContent = o; input.value = ''; menu.style.display = 'none'; };
      menu.appendChild(d);
    }
    menu.style.display = 'block';
  };
  input.addEventListener('focus', show); input.addEventListener('click', show); input.addEventListener('input', show);
  input.addEventListener('keydown', e => { if (e.key === 'Escape') menu.style.display = 'none'; });
}
</script>"""


def _combo(html_id, label):
    return f"""<div class="select__container"><label id="{html_id}-label" for="{html_id}">{label}*</label>
      <div class="select__value-container"><div class="select__single-value"></div>
      <input class="select__input" id="{html_id}" role="combobox" aria-autocomplete="list" type="text">
      <input required tabindex="-1" aria-hidden="true" class="requiredInput" value=""></div>
      <div role="listbox" style="display:none"></div></div>"""


GREENHOUSE = f"""<!doctype html><html><body><form onsubmit="window.submitted = true; return false;">
<label for="first_name">First Name*</label><input id="first_name" type="text">
<label for="last_name">Last Name*</label><input id="last_name" type="text">
<label for="email">Email*</label><input id="email" type="text">
{_combo("country", "Country")}
<div class="input-wrapper"><label for="phone">Phone*</label><div class="iti"><input id="phone" type="tel"></div></div>
{_combo("candidate-location", "Location (City)")}
<div><button type="button">Attach</button><label class="visually-hidden" for="resume">Attach</label>
  <input id="resume" type="file" style="position:absolute;width:1px;height:1px;opacity:0"></div>
{_combo("question_1", "Are you legally authorized to work in the country where the job is located?")}
<button type="submit">Submit application</button>
</form>{COMBO_JS}<script>
combo('country', ['United States +1', 'Australia +61', 'New Zealand +64']);
combo('candidate-location', ['Sydney, New South Wales, Australia', 'Sydney, Nova Scotia, Canada']);
combo('question_1', ['Yes, I am currently legally authorized to work in the country where the job is located.',
                     'No, I am not currently legally authorized to work in the country where the job is located.']);
</script></body></html>"""

LEVER = """<!doctype html><html><body><form onsubmit="window.submitted = true; return false;"><ul>
<li class="application-question"><label><div class="application-label">Resume/CV ✱</div>
  <div class="application-field"><input type="file" name="resume" id="resume-upload-input"></div></label></li>
<li class="application-question"><label><div class="application-label">Full name ✱</div>
  <div class="application-field"><input type="text" name="name"></div></label></li>
<li class="application-question"><label><div class="application-label">Email ✱</div>
  <div class="application-field"><input type="email" name="email"></div></label></li>
<li class="application-question"><label><div class="application-label">Current location ✱</div>
  <div class="application-field"><input type="text" name="location" id="location-input">
  <div class="dropdown-container"><div class="dropdown-results"></div>
  <div class="dropdown-no-results" style="display:none">No location found</div></div></div></label></li>
<li class="application-question"><label><div class="application-label">Current company</div>
  <div class="application-field"><input type="text" name="org"></div></label></li>
<li class="application-question custom-question"><div><div class="application-label"><div class="text">
  Are you legally authorized to work in the country for which you are applying?✱</div></div>
  <div class="application-field"><ul>
  <li><label><input type="radio" name="cards[1c719ca9-5069-4afe-9e82-39ca420e0edb][field0]" value="Yes">
    <span class="application-answer-alternative">Yes</span></label></li>
  <li><label><input type="radio" name="cards[1c719ca9-5069-4afe-9e82-39ca420e0edb][field0]" value="No">
    <span class="application-answer-alternative">No</span></label></li></ul></div></div></li>
<li class="application-question custom-question"><div><div class="application-label"><div class="text">
  Please tell us how you heard about this opportunity.✱</div></div>
  <div class="application-field"><select name="cards[a6197d84-549e-4a91-8bb0-6af972510013][field0]">
  <option>Select...</option><option>LinkedIn</option><option>Seek</option></select></div></div></li>
<li class="application-question"><div class="application-label">Additional information</div>
  <div class="application-field"><textarea name="comments"></textarea></div></li>
</ul><button type="submit">Submit application</button></form>
<script>
const loc = document.getElementById('location-input'), res = document.querySelector('.dropdown-results');
loc.addEventListener('input', () => setTimeout(() => {
  res.innerHTML = '';
  for (const t of ['Sydney NSW, Australia', 'Sydney, Nova Scotia, Canada'])
    if (loc.value && t.toLowerCase().includes(loc.value.toLowerCase())) {
      const d = document.createElement('div'); d.textContent = t;
      d.onclick = () => { loc.value = t; res.innerHTML = ''; }; res.appendChild(d);
    }
}, 300));
</script></body></html>"""

SEEK = """<!doctype html><html><body><form onsubmit="window.submitted = true; return false;">
<fieldset><legend>Resumé</legend>
 <label><input type="radio" name="resume-method" value="upload"
   onchange="document.getElementById('up').style.display='block'"> Upload a resumé</label>
 <label><input type="radio" name="resume-method" value="select"> Select a resumé</label>
 <label><input type="radio" name="resume-method" value="none" checked> Don't include a resumé</label>
 <div id="up" style="display:none"><label for="rf">Upload resume file</label><input id="rf" type="file"></div>
</fieldset>
<fieldset><legend>Cover letter</legend>
 <label><input type="radio" name="cl-method" value="upload"> Upload a cover letter</label>
 <label><input type="radio" name="cl-method" value="write"
   onchange="document.getElementById('wr').style.display='block'"> Write a cover letter</label>
 <label><input type="radio" name="cl-method" value="none" checked> Don't include a cover letter</label>
 <div id="wr" style="display:none"><label for="clt">Cover letter</label><textarea id="clt"></textarea></div>
</fieldset>
<button type="button" onclick="window.continued = true">Continue</button>
</form></body></html>"""


def _setup(tmp_path, monkeypatch, html, name, cover_pdf=True):
    tailored = tmp_path / "tailored"
    tailored.mkdir(exist_ok=True)
    (tailored / "a.txt").write_text("Jane Doe\nBackend Engineer at Acme")
    (tailored / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    (tailored / "cl.txt").write_text("Dear Hiring Manager,\nI build backend systems.\nJane")
    if cover_pdf:
        (tailored / "cl.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "profile.json").write_text(json.dumps(PROFILE))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    for key, value in {"PROFILE_PATH": tmp_path / "profile.json", "LOG_DIR": tmp_path,
                       "APPLY_WORKER_DIR": tmp_path / "apply",
                       "LOCAL_BROWSER_PROFILE_DIR": tmp_path / "browser"}.items():
        monkeypatch.setattr(config, key, value)
    form = tmp_path / f"{name}.html"
    form.write_text(html)
    return {"title": "Backend Engineer", "site": "Acme", "url": form.as_uri(), "application_url": None,
            "tailored_resume_path": str(tailored / "a.txt"), "cover_letter_path": str(tailored / "cl.txt"),
            "fit_score": 9}


class Gemini:
    """Records the fields sent to the AI and answers with queued actions."""

    def __init__(self, answer=None):
        self.sent: list[list[dict]] = []
        self.answer = answer or (lambda fields: [])

    def __call__(self, prompt, api_key, model):
        fields = json.loads(prompt.split("FORM FIELDS:\n", 1)[1])
        self.sent.append(fields)
        return {"actions": self.answer(fields), "needs_attention": []}


# ---------------------------------------------------------------------------
# Unit: adapter rules
# ---------------------------------------------------------------------------

def test_seek_picks_upload_and_cover_letter_options():
    fields = [
        {"id": "a", "type": "radio", "label": "Upload a resumé", "group": "Resumé"},
        {"id": "b", "type": "radio", "label": "Select a resumé", "group": "Resumé"},
        {"id": "c", "type": "radio", "label": "Don't include a resumé", "group": "Resumé", "checked": True},
        {"id": "d", "type": "radio", "label": "Upload a cover letter", "group": "Cover letter"},
        {"id": "e", "type": "radio", "label": "Write a cover letter", "group": "Cover letter"},
        {"id": "f", "type": "radio", "label": "Don't include a cover letter", "group": "Cover letter"},
    ]
    text = {"{{COVER_LETTER_TEXT}}": "Dear..."}
    actions, remaining = adapters.seek_actions(fields, text, {"{{RESUME_PDF}}": "r"})
    assert [a["id"] for a in actions] == ["a", "e"] and remaining == []  # no PDF: write it
    actions, _ = adapters.seek_actions(fields, text, {"{{RESUME_PDF}}": "r", "{{COVER_LETTER_PDF}}": "c"})
    assert [a["id"] for a in actions] == ["a", "d"]


def test_lever_cover_letter_goes_in_additional_information():
    comments = {"id": "x", "type": "textarea", "label": "Additional information", "name": "comments"}
    actions, remaining = adapters.lever_actions([comments], {"{{COVER_LETTER_TEXT}}": "Dear..."}, {})
    assert actions == [{"id": "x", "action": "fill", "value": "{{COVER_LETTER_TEXT}}"}] and remaining == []
    cover = {"id": "y", "type": "file", "label": "Cover letter"}
    actions, _ = adapters.lever_actions([comments, cover], {"{{COVER_LETTER_TEXT}}": "Dear..."}, {})
    assert actions == []  # there's a proper place for it


@pytest.mark.parametrize("phone, country, expected", [
    ("0412 345 678", "Australia", "+61 412 345 678"),
    ("(02) 9876 5432", "", "+61 298765432"),
    ("+61 400 000 000", "Australia", "+61 400 000 000"),
    ("555-1234", "USA", ""),
])
def test_international_phone(phone, country, expected):
    assert le._international_phone(phone, country) == expected


# ---------------------------------------------------------------------------
# In a real browser
# ---------------------------------------------------------------------------

@headed
def test_greenhouse_form(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    job = _setup(tmp_path, monkeypatch, GREENHOUSE, "gh")
    work_rights = "Yes, I am currently legally authorized to work in the country where the job is located."
    gemini = Gemini(lambda fields: [{"id": f["id"], "action": "choose", "value": work_rights}
                                    for f in fields if f["type"] == "combobox"])
    monkeypatch.setattr(le, "_call_gemini", gemini)
    seen = {}

    def gate(page, needs, dry_run):
        seen.update(page.evaluate("""() => ({
            first: first_name.value, last: last_name.value, email: email.value, phone: phone.value,
            chosen: [...document.querySelectorAll('.select__single-value')].map(e => e.textContent),
            resume: resume.files.length, submitted: !!window.submitted})"""))
        seen["needs"] = needs
        return "manual"

    assert le.run_job(job, gate=gate)[0] == "manual"
    assert (seen["first"], seen["last"], seen["email"]) == ("Jane", "Doe", "jane@example.com")
    assert seen["phone"] == "+61 412 345 678"     # the country picker reads the +61
    assert seen["chosen"] == ["Australia +61", "Sydney, New South Wales, Australia", work_rights]
    assert seen["resume"] == 1 and seen["submitted"] is False
    # Only the screening question reached the AI, with its options read from the open menu
    assert len(gemini.sent) == 1
    [question] = gemini.sent[0]
    assert question["type"] == "combobox" and len(question["options"]) == 2
    assert "Jane" not in json.dumps(gemini.sent)


@headed
def test_lever_form(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    job = _setup(tmp_path, monkeypatch, LEVER, "lever", cover_pdf=False)
    monkeypatch.setattr(le, "detect_ats", lambda url: "lever")  # a local file stands in for jobs.lever.co
    gemini = Gemini()
    monkeypatch.setattr(le, "_call_gemini", gemini)
    seen = {}

    def gate(page, needs, dry_run):
        seen.update(page.evaluate("""() => ({
            name: document.querySelector('[name=name]').value, org: document.querySelector('[name=org]').value,
            location: document.querySelector('[name=location]').value,
            comments: document.querySelector('[name=comments]').value,
            resume: document.querySelector('[name=resume]').files.length, submitted: !!window.submitted})"""))
        return "manual"

    assert le.run_job(job, gate=gate)[0] == "manual"
    assert seen["name"] == "Jane Doe" and seen["org"] == "Acme" and seen["resume"] == 1
    assert seen["location"] == "Sydney NSW, Australia"  # picked from the suggestions
    assert seen["comments"].startswith("Dear Hiring Manager")
    assert seen["submitted"] is False
    [sent] = gemini.sent
    by_type = {f["type"]: f for f in sent}
    # Questions keep their own text, not the option labels or the select's options
    assert by_type["radio"]["group"].startswith("Are you legally authorized")
    assert by_type["select"]["label"].startswith("Please tell us how you heard")
    assert "LinkedIn" not in by_type["select"]["label"]
    assert {f["type"] for f in sent} == {"radio", "select"}


@headed
def test_seek_document_step_needs_no_ai(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    job = _setup(tmp_path, monkeypatch, SEEK, "seek", cover_pdf=False)
    monkeypatch.setattr(le, "detect_ats", lambda url: "seek")  # a local file stands in for seek.com.au
    gemini = Gemini()
    monkeypatch.setattr(le, "_call_gemini", gemini)
    seen = {}

    def gate(page, needs, dry_run):
        seen.update(page.evaluate("""() => ({resume: rf.files.length, cover: clt.value,
            continued: !!window.continued, submitted: !!window.submitted})"""))
        return "manual"

    assert le.run_job(job, gate=gate)[0] == "manual"
    assert seen["resume"] == 1                       # revealed by "Upload a resumé", then filled
    assert seen["cover"].startswith("Dear Hiring Manager")
    assert seen["continued"] is True and seen["submitted"] is False
    assert gemini.sent == []


@headed
def test_choose_never_presses_enter(tmp_path, monkeypatch):
    """An autocomplete with no suggestions must not submit the form."""
    pytest.importorskip("playwright")
    html = LEVER.replace("for (const t of ['Sydney NSW, Australia', 'Sydney, Nova Scotia, Canada'])",
                         "for (const t of [])")
    job = _setup(tmp_path, monkeypatch, html, "lever_empty", cover_pdf=False)
    monkeypatch.setattr(le, "_call_gemini", Gemini())
    seen = {}

    def gate(page, needs, dry_run):
        seen["submitted"] = page.evaluate("() => !!window.submitted")
        seen["needs"] = needs
        return "manual"

    le.run_job(job, gate=gate)
    assert seen["submitted"] is False
    assert any("Current location" in n for n in seen["needs"])


# ---------------------------------------------------------------------------
# Upload boxes labelled only "Upload" (PageUp and similar)
# ---------------------------------------------------------------------------

FILES = {"{{RESUME_PDF}}": "/u/Jane_Doe_Resume.pdf", "{{COVER_LETTER_PDF}}": "/u/Jane_Doe_Cover_Letter.pdf"}


def _box(i, label="Upload", context=""):
    return {"id": i, "type": "file", "label": label, "context": context}


@pytest.mark.parametrize("boxes, expected", [
    # The row says what each box is for
    ([_box("a", context="Resume * Upload (max 5MB)"), _box("b", context="Cover letter Upload (max 5MB)")],
     {"a": "{{RESUME_PDF}}", "b": "{{COVER_LETTER_PDF}}"}),
    # One labelled Resume, one unlabelled: the other is the cover letter
    ([_box("a", "Resume/CV"), _box("b", "Attach")], {"a": "{{RESUME_PDF}}", "b": "{{COVER_LETTER_PDF}}"}),
    # A row already showing our file was uploaded: leave it
    ([_box("a", context="Resume Jane_Doe_Resume.pdf Remove")], {"a": None}),
    # Never the resume twice
    ([_box("a", "Resume"), _box("b", "Resume upload")], {"a": "{{RESUME_PDF}}"}),
    # The page's only upload box takes the resume, even when its text mentions both or neither
    ([_box("a", context="Please attach your resume and cover letter")], {"a": "{{RESUME_PDF}}"}),
    ([_box("a", "Attach")], {"a": "{{RESUME_PDF}}"}),
    # Two boxes and neither says which: ask the AI
    ([_box("a", "Attach"), _box("b", "Attach")], {}),
])
def test_upload_targets(boxes, expected):
    from applypilot.apply.answers import _upload_actions
    assert _upload_actions(boxes, FILES) == expected


PAGEUP = """<!doctype html><html><body><form onsubmit="window.submitted = true; return false;">
<table>
 <tr id="r1"><td><b>Resume</b> *</td>
   <td><label for="f1">Upload</label><input type="file" id="f1"></td><td class="done"></td></tr>
 <tr id="r2"><td><b>Cover letter</b></td>
   <td><label for="f2">Upload</label><input type="file" id="f2"></td><td class="done"></td></tr>
</table>
<button type="submit">Submit application</button></form>
<script>
window.uploads = [];
for (const id of ['f1', 'f2']) {
  const input = document.getElementById(id);
  input.addEventListener('change', () => {
    // Like PageUp: upload straight away, show the file name, empty the box
    window.uploads.push(id + ':' + input.files[0].name);
    input.closest('tr').querySelector('.done').textContent = input.files[0].name;
    input.value = '';
  });
}
</script></body></html>"""


@headed
def test_pageup_style_uploads_go_in_the_right_boxes_once(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    job = _setup(tmp_path, monkeypatch, PAGEUP, "pageup")
    monkeypatch.setattr(le, "_call_gemini", Gemini())
    seen = []

    def gate(page, needs, dry_run):
        seen.append(page.evaluate("() => window.uploads.slice()"))
        return "rescan" if len(seen) == 1 else "manual"   # 'Fill this page again' once

    assert le.run_job(job, gate=gate)[0] == "manual"
    assert seen[0] == ["f1:Jane_Doe_Resume.pdf", "f2:Jane_Doe_Cover_Letter.pdf"]
    assert seen[1] == seen[0]  # nothing uploaded again
    # Only this job's documents are in the visible upload folder
    assert sorted(p.name for p in le.staged_uploads()) == ["Jane_Doe_Cover_Letter.pdf", "Jane_Doe_Resume.pdf"]


COVER_TEXT_NEARBY = """<!doctype html><html><body><form>
<div class="section"><h3>Your application</h3>
 <div class="row"><label for="cl">Cover letter (optional)</label><textarea id="cl"></textarea></div>
 <div class="row"><div><button type="button">Attach</button><label for="f1">Attach</label>
   <input type="file" id="f1"></div></div>
</div></form></body></html>"""


def test_upload_box_reads_only_its_own_row(tmp_path):
    """Regression: a lone 'Attach' box took its section's text ('Cover letter') and got the cover letter."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from applypilot.apply.answers import _upload_actions
    from applypilot.browser import launch_chromium
    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page()
        page.set_content(COVER_TEXT_NEARBY)
        fields, _, _ = le._scan(page)
        browser.close()
    [box] = [f for f in fields if f["type"] == "file"]
    assert "cover" not in (box.get("context") or "").lower()
    assert _upload_actions([box], FILES) == {box["id"]: "{{RESUME_PDF}}"}


def test_choose_retries_when_the_pick_is_lost():
    """Regression (flaky on CI): a lost click left the typed text in the box, which passed for the pick."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from applypilot.browser import launch_chromium
    page_html = f"""<!doctype html><html><body>{_combo("country", "Country")}{COMBO_JS}<script>
    combo('country', ['United States +1', 'Australia +61']);
    let lost = false;  // a busy page drops the first click on an option
    document.addEventListener('click', e => {{
      if (!lost && e.target.getAttribute('role') === 'option') {{ lost = true; e.stopImmediatePropagation(); }}
    }}, true);
    </script></body></html>"""
    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page()
        page.set_content(page_html)
        le._choose(page.main_frame, page.locator("#country"), "Australia +61")
        chosen = page.locator(".select__single-value").inner_text()
        browser.close()
    assert chosen == "Australia +61"

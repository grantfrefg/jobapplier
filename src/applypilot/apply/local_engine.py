"""Free local apply engine: Playwright + Gemini (BYOK), human-submitted.

Selected with EXECUTION_ENGINE=local_playwright. Instead of spawning the paid
Claude Code CLI, this engine:

  1. Launches a visible local Chromium (headless=False) with a persistent
     profile, so logins to SEEK / Workday / etc. survive between runs.
  2. Opens the job's application URL and extracts a compact description of
     every form control (label, type, options, current value) from the DOM,
     including inside iframes (Greenhouse embeds).
  3. Fills standard fields (name, contact, address, uploads) and questions
     it has answered before (the answer bank, see answers.py) in code, then
     sends the remaining fields, plus a *redacted* profile and resume, to Gemini
     via the REST generateContent endpoint, and receives fill actions.
     Personal identifiers (name, email, phone, street address, postcode,
     tax / ID numbers, URLs) are sent only as {{PLACEHOLDER}} tokens and are
     swapped back in locally, so they never reach the cloud API.
  4. Fills text boxes, drop-downs, radios / checkboxes and file uploads, and
     clicks only "Next" / "Continue" style buttons to advance multi-step forms.
  5. SAFETY GATE: it never clicks anything that looks like Submit / Apply /
     Send. It highlights those controls, freezes on a terminal prompt (or
     page.pause() without a TTY), and leaves the browser open so the user can
     review, edit and press Submit themselves.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from applypilot import budget, config
from applypilot.apply import adapters, answers
from applypilot.apply.dashboard import add_event, update_state
from applypilot.database import apply_link

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_TIMEOUT = 120
_GEMINI_MAX_RETRIES = 4

# Cap on how many multi-step pages we will auto-advance through
_MAX_STEPS = 8
# How long to wait for a Next/Continue button to become clickable. Real ATS
# pages (and busy machines) often need more than Playwright's usual 5s.
_NEXT_CLICK_TIMEOUT_MS = 15000
# Cap on field descriptors sent to Gemini per page
_MAX_FIELDS = 150
_MAX_RESUME_CHARS = 8000
_MAX_AD_CHARS = 3000  # job description shown to the form AI
# jobs.site -> the name application forms use in "How did you hear about us?"
_BOARD_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "jora": "Jora", "seek": "SEEK",
                "glassdoor": "Glassdoor", "zip_recruiter": "ZipRecruiter", "google": "Google Jobs"}

# ---------------------------------------------------------------------------
# Safety gate: button classification
# ---------------------------------------------------------------------------

# Anything matching this is NEVER clicked by automation. Deny wins over allow.
_SUBMIT_RE = re.compile(
    r"\b(submit|apply|send|finish|complete|confirm|done|place order|i agree and)\b",
    re.IGNORECASE,
)
# Only controls matching this (and not the deny list) may be auto-clicked.
_NEXT_RE = re.compile(
    r"^\s*(next|continue|save\s*(and|&)\s*continue|next\s*step|review|proceed)\b",
    re.IGNORECASE,
)


def classify_control(text: str) -> str | None:
    """Classify a clickable control by its visible text.

    Returns "submit" (blocked, needs the human), "next" (safe to auto-click),
    or None (ignored).
    """
    text = (text or "").strip()
    if not text:
        return None
    if _SUBMIT_RE.search(text):
        return "submit"
    if _NEXT_RE.search(text):
        return "next"
    return None


# ---------------------------------------------------------------------------
# Privacy: placeholders for sensitive profile values
# ---------------------------------------------------------------------------

# Profile keys (anywhere in profile.json) whose values must never leave the
# machine in clear text. Matched case-insensitively against the key name.
_SENSITIVE_KEY_RE = re.compile(
    r"(tax|tfn|ssn|social_security|sin_number|abn|passport|licen[cs]e|"
    r"date_of_birth|dob|birth|medicare|national_id|bank|account_number)",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


def _looks_like_identifier(value) -> bool:
    """IDs, DOBs and account numbers contain digits; Yes/No answers don't."""
    return isinstance(value, (str, int)) and any(c.isdigit() for c in str(value))


def _redact_phone(match: re.Match) -> str:
    # Require a realistic digit count so year ranges ("2019 - 2021") survive
    digits = sum(c.isdigit() for c in match.group(0))
    return "{{PHONE}}" if digits >= 9 else match.group(0)


def _walk_sensitive(obj, prefix: str = "") -> list[tuple[str, str]]:
    """Find (KEY_PATH, value) for sensitive-looking keys nested anywhere."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}_{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                found.extend(_walk_sensitive(v, path))
            elif _SENSITIVE_KEY_RE.search(str(k)) and _looks_like_identifier(v):
                found.append((path, str(v)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_walk_sensitive(v, f"{prefix}_{i}"))
    return found


def _international_phone(phone: str, country: str) -> str:
    """+61 format for phone fields with a country picker (0412 345 678 -> +61 412 345 678)."""
    phone = (phone or "").strip()
    if phone.startswith("+"):
        return phone
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("0") and (not country or "australia" in country.lower()):
        rest = digits[1:]
        mobile = len(rest) == 9 and rest.startswith("4")
        return "+61 " + (f"{rest[:3]} {rest[3:6]} {rest[6:]}" if mobile else rest)
    return ""


def build_placeholders(profile: dict) -> dict[str, str]:
    """Map {{TOKEN}} -> real value for every identifier we keep local."""
    personal = profile.get("personal", {})
    full_name = personal.get("full_name", "").strip()
    parts = full_name.split()
    phone = personal.get("phone", "")

    values = {
        "FULL_NAME": full_name,
        "FIRST_NAME": parts[0] if parts else "",
        "LAST_NAME": parts[-1] if len(parts) > 1 else "",
        "PREFERRED_NAME": personal.get("preferred_name", "") or (parts[0] if parts else ""),
        "EMAIL": personal.get("email", ""),
        "PHONE": phone,
        "PHONE_DIGITS": "".join(c for c in phone if c.isdigit()),
        "PHONE_INTL": _international_phone(phone, personal.get("country", "")),
        "STREET_ADDRESS": personal.get("address", ""),
        "POSTAL_CODE": personal.get("postal_code", ""),
        "LINKEDIN_URL": personal.get("linkedin_url", ""),
        "GITHUB_URL": personal.get("github_url", ""),
        "PORTFOLIO_URL": personal.get("portfolio_url", ""),
        "WEBSITE_URL": personal.get("website_url", ""),
    }
    for path, value in _walk_sensitive(profile):
        token = "SENSITIVE_" + re.sub(r"[^A-Z0-9]+", "_", path.upper()).strip("_")
        values[token] = value

    return {f"{{{{{k}}}}}": v for k, v in values.items() if v}


def redact(text: str, placeholders: dict[str, str]) -> str:
    """Replace every known sensitive value (and stray emails/phones) with tokens."""
    if not text:
        return ""
    # Longest values first so "Jane Q Doe" is replaced before "Jane"
    for token, value in sorted(placeholders.items(), key=lambda kv: -len(kv[1])):
        if len(value) < 3:
            continue
        text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", token, text, flags=re.IGNORECASE)
    text = _EMAIL_RE.sub("{{EMAIL}}", text)
    text = _PHONE_RE.sub(_redact_phone, text)
    return text


def unredact(value: str, local_values: dict[str, str]) -> str | None:
    """Swap {{TOKENS}} back to real values. None if an unknown token remains."""
    if not isinstance(value, str):
        value = str(value)
    for token, real in local_values.items():
        value = value.replace(token, real)
    if re.search(r"\{\{[A-Z0-9_]+\}\}", value):
        return None
    return value


def redacted_profile(profile: dict, placeholders: dict[str, str]) -> dict:
    """Profile safe to send to Gemini: identifiers as tokens, no password."""
    reverse = {v: k for k, v in placeholders.items()}

    def _clean(obj, key: str = ""):
        if isinstance(obj, dict):
            return {
                k: _clean(v, k) for k, v in obj.items()
                if "password" not in k.lower()
            }
        if isinstance(obj, list):
            return [_clean(v, key) for v in obj]
        if isinstance(obj, str) and obj in reverse:
            return reverse[obj]
        if _SENSITIVE_KEY_RE.search(key) and _looks_like_identifier(obj):
            return "{{REDACTED}}"
        return obj

    safe = _clean(profile)
    # Name pieces are derived, not stored; advertise them for split-name forms.
    safe.setdefault("personal", {})
    for tok in ("{{FIRST_NAME}}", "{{LAST_NAME}}", "{{PHONE_DIGITS}}"):
        if tok in placeholders:
            safe["personal"][tok.strip("{}").lower()] = tok
    # Drop resume_facts — only useful for tailoring, not form filling
    safe.pop("resume_facts", None)
    return safe


# ---------------------------------------------------------------------------
# ATS detection and hints
# ---------------------------------------------------------------------------

_ATS_HINTS = {
    "seek": (
        "SEEK (Australia). Steps: choose documents (resume: prefer 'Upload a resume' "
        "-> {{RESUME_PDF}}; cover letter: 'Write a cover letter' -> {{COVER_LETTER_TEXT}} "
        "or upload {{COVER_LETTER_PDF}}), employer questions, update SEEK profile, review. "
        "Employer questions often include 'right to work in Australia' (pick the option that "
        "matches work_authorization, e.g. citizen / permanent resident / visa with full work "
        "rights), years of experience, salary expectation (AUD), notice period, driver's "
        "licence, police check. Pick the closest exact option text."
    ),
    "greenhouse": (
        "Greenhouse. First Name / Last Name / Email / Phone, resume file input (often hidden; "
        "still use upload), cover letter upload or textarea, LinkedIn/website URLs, custom "
        "questions as selects or textareas, optional EEO section (use eeo_voluntary values)."
    ),
    "lever": (
        "Lever. Single 'Full name' field, email, phone, 'Current company', resume upload, "
        "URLs (LinkedIn, GitHub, Portfolio, Other), 'Additional information' textarea "
        "(use {{COVER_LETTER_TEXT}} if no dedicated cover letter field), custom cards."
    ),
    "workday": (
        "Workday. Multi-step: My Information, My Experience (resume upload {{RESUME_PDF}}), "
        "Application Questions, Voluntary Disclosures, Self Identify, Review. Drop-downs are "
        "custom comboboxes: use action 'choose' with the exact option text. Sponsorship and "
        "work authorization questions are usually Yes/No."
    ),
}


def detect_ats(url: str) -> str | None:
    u = (url or "").lower()
    if "seek.com" in u:
        return "seek"
    if "greenhouse.io" in u or "gh_jid=" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "myworkdayjobs.com" in u or "workday" in u:
        return "workday"
    return None


# ---------------------------------------------------------------------------
# DOM extraction (runs in the page)
# ---------------------------------------------------------------------------

_EXTRACT_JS = r"""
(prefix) => {
  const clean = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const visible = el => {
    if (el.type === 'file') return true;  // file inputs are often hidden behind a button
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const byId = id => id ? document.getElementById(id) : null;
  // Text of an element without the text of any form controls inside it
  // (a <select>'s parent would otherwise include every option).
  const ownText = node => {
    let t = node.innerText || '';  // innerText skips hidden status messages
    for (const x of node.querySelectorAll('select, textarea, [role=listbox]')) {
      const inner = x.innerText;
      if (inner) t = t.split(inner).join(' ');
    }
    return clean(t);
  };
  // The question an input belongs to, from its surrounding container
  // (Lever's .application-label, fieldset legends, form-group labels). Never
  // the text of the input's own option label ("Yes" / "No").
  const questionOf = el => {
    const own = el.closest('label');
    const sel = 'legend, .application-label, [class*="question-label"], [class*="question__label"], ' +
                'label:not(:has(input, select, textarea)), [class*="label"]:not(:has(input, select, textarea))';
    let p = el.parentElement;
    for (let i = 0; i < 7 && p && p !== document.body; i++, p = p.parentElement) {
      for (const q of p.querySelectorAll(sel)) {
        if (q.contains(el) || (own && own.contains(q)) || q.closest('label') === own && own) continue;
        if (q.querySelector('input[type=radio], input[type=checkbox]')) continue;
        const t = ownText(q);
        if (t) return t;
      }
      if (p.matches('li.application-question, fieldset, [role=group], [role=radiogroup], .form-group, .field')) break;
    }
    return '';
  };
  const labelOf = el => {
    let t = '';
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) t = l.innerText;
    }
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map(i => byId(i)?.innerText || '').join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const l = el.closest('label'); if (l) t = ownText(l); }
    if (!t && el.type !== 'radio' && el.type !== 'checkbox') t = questionOf(el);
    if (!t) t = el.getAttribute('placeholder') || '';
    if (!t) {
      let p = el.parentElement;
      for (let i = 0; i < 3 && p && !clean(t); i++) { t = ownText(p); p = p.parentElement; }
    }
    return clean(t);
  };
  const groupOf = el => {
    const q = questionOf(el);
    if (q) return q;
    const fs = el.closest('fieldset');
    const lg = fs && fs.querySelector('legend');
    if (lg) return clean(lg.innerText);
    const rg = el.closest('[role=radiogroup],[role=group]');
    if (rg) return clean(rg.getAttribute('aria-label') ||
                         byId(rg.getAttribute('aria-labelledby'))?.innerText || '');
    let p = el.parentElement;
    const own = el.closest('label');
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      for (const q of p.querySelectorAll('label:not(:has(input)), legend, h3, h4, p, span')) {
        if (q.contains(el) || (own && own.contains(q)) || q.closest('label')) continue;
        if (clean(q.innerText)) return clean(q.innerText);
      }
    }
    return '';
  };

  // Drop ids from a previous scan so hidden/stale elements can't collide
  for (const el of document.querySelectorAll('[data-ap-id]')) el.removeAttribute('data-ap-id');

  // An open dialog with form fields (LinkedIn Easy Apply, modal application
  // forms) is the form: ignore the page behind it, its search boxes and buttons.
  const dialogs = [...document.querySelectorAll('[role=dialog], [aria-modal=true], dialog[open]')]
    .filter(d => visible(d) && d.querySelector('input:not([type=hidden]), select, textarea'));
  const root = dialogs.length ? dialogs[dialogs.length - 1] : document;

  const fields = [];
  const skip = new Set(['hidden', 'submit', 'button', 'image', 'reset', 'password', 'search']);
  let n = 0;
  const sel = 'input, select, textarea, [role=combobox]:not(input), [contenteditable=true]';
  for (const el of root.querySelectorAll(sel)) {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.type || 'text').toLowerCase() : tag;
    if (skip.has(type) || el.disabled || el.readOnly || !visible(el)) continue;
    // React-Select & co. keep a hidden "required" twin of each drop-down
    if (type !== 'file' && el.getAttribute('aria-hidden') === 'true') continue;
    const id = `${prefix}-${n++}`;
    el.setAttribute('data-ap-id', id);
    const f = { id, type, label: labelOf(el), name: el.name || '', required: !!(el.required ||
                el.getAttribute('aria-required') === 'true') };
    if (el.id && el.id.length <= 60) f.html_id = el.id;
    if (el.closest('.iti')) f.intl_phone = true;  // intl-tel-input: wants +61 format
    if (tag === 'select') {
      f.options = [...el.options].map(o => clean(o.text)).filter(Boolean).slice(0, 60);
      f.value = clean(el.selectedOptions[0]?.text || '');
    } else if (type === 'checkbox' || type === 'radio') {
      f.group = groupOf(el);
      f.checked = el.checked;
    } else if (type === 'file') {
      f.accept = el.accept || '';
      f.has_file = el.files && el.files.length > 0;
      // Upload boxes are often labelled just "Upload" or "Attach"; what they
      // are for ("Cover letter", "Resume / CV") is in their own row: the
      // nearest container with more than button words, stopping before one
      // that holds another field (its text would describe that field).
      const generic = new RegExp('attach|upload|browse|choose|select|files?|drop|drag|here|max|mb|' +
                                 'pdf|docx?|\\b(or|and|to|a)\\b|[^a-z]', 'g');
      let box = el.parentElement, around = '';
      for (let i = 0; i < 6 && box && box !== document.body; i++, box = box.parentElement) {
        const others = [...box.querySelectorAll('input, select, textarea')]
          .filter(x => x !== el && x.type !== 'hidden' && !(x.type === 'file' && x === el));
        if (others.length) break;
        const t = clean(box.innerText);
        if (t && t.toLowerCase().replace(generic, '')) { around = t; break; }
      }
      if (around) f.context = around.slice(0, 160);
    } else if (el.getAttribute('role') === 'combobox' && tag !== 'input') {
      f.type = 'combobox';
      f.value = clean(el.innerText);
    } else if (tag === 'input' && (el.getAttribute('role') === 'combobox' ||
               el.getAttribute('aria-autocomplete') === 'list' || el.classList.contains('select__input'))) {
      // Searchable drop-down (React-Select, Greenhouse, Lever location)
      f.type = 'combobox';
      const box = el.closest('[class*="container"]');
      const chosen = box && box.querySelector('[class*="single-value"], [class*="singleValue"]');
      f.value = clean(el.value || (chosen ? chosen.innerText : ''));
    } else {
      f.value = clean(el.value || el.innerText || '');
    }
    fields.push(f);
  }

  const controls = [];
  const csel = 'button, input[type=submit], input[type=button], a[role=button], [role=button], a';
  let m = 0;
  for (const el of root.querySelectorAll(csel)) {
    if (!visible(el)) continue;
    const text = clean(el.innerText || el.value || el.getAttribute('aria-label') || '');
    if (!text || text.length > 60) continue;
    const id = `${prefix}-c${m++}`;
    el.setAttribute('data-ap-id', id);
    controls.push({ id, text, tag: el.tagName.toLowerCase(),
                    href: el.tagName === 'A' ? el.href : '' });
  }
  return { fields, controls };
}
"""

_HIGHLIGHT_JS = r"""
(ids) => {
  for (const id of ids) {
    const el = document.querySelector(`[data-ap-id="${id}"]`);
    if (el) { el.style.outline = '4px solid #e11d48'; el.style.outlineOffset = '2px'; }
  }
}
"""


def _scan(page) -> tuple[list[dict], list[dict], dict]:
    """Extract fields and clickable controls from every frame.

    Returns (fields, controls, frame_by_id) where frame_by_id maps each
    data-ap-id to the frame that owns it.
    """
    fields: list[dict] = []
    controls: list[dict] = []
    frame_by_id: dict = {}
    for i, frame in enumerate(page.frames):
        try:
            data = frame.evaluate(_EXTRACT_JS, f"f{i}")
        except Exception as e:  # cross-origin / detached frames
            logger.debug("Frame %d scan failed: %s", i, e)
            continue
        for f in data["fields"]:
            frame_by_id[f["id"]] = frame
            fields.append(f)
        for c in data["controls"]:
            frame_by_id[c["id"]] = frame
            controls.append(c)
    return fields[:_MAX_FIELDS], controls, frame_by_id


# ---------------------------------------------------------------------------
# Gemini (REST, BYOK)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You fill out job application forms for an applicant.
You receive: the applicant profile, their resume, the job (title, where it was found and the
ad's description), and a JSON list of form fields extracted from the page (id, type, label,
options, current value).

Return JSON: {"actions": [{"id": "...", "action": "...", "value": "..."}],
              "needs_attention": ["short note per required field you could not answer"]}

Actions:
- "fill":   text/email/tel/url/number/date/textarea/contenteditable -> value is the text
- "select": native <select> -> value is the EXACT option text from "options"
- "check":  checkbox or radio to tick -> value ""  (only list ones that must be ON)
- "uncheck": checkbox to clear -> value ""
- "choose": searchable drop-down (type "combobox") -> value is the EXACT option text; if
            "more_options" is true the list was cut short: give the option text you expect
            (e.g. "Australia")
- "upload": file input -> value "{{RESUME_PDF}}" or "{{COVER_LETTER_PDF}}"

Rules:
- Personal identifiers appear as {{TOKENS}}. Use the tokens VERBATIM as values
  (e.g. "{{EMAIL}}", "{{FIRST_NAME}}"). Never invent or guess real values.
- For a "paste your resume" box use "{{RESUME_TEXT}}"; for a cover letter box use
  "{{COVER_LETTER_TEXT}}".
- Only use facts from the profile/resume. Never fabricate experience.
- Skip search boxes and anything unrelated to the application (newsletters, job alerts,
  marketing opt-ins: leave unticked). Tick a required privacy-policy / terms consent that the
  application can't be sent without.
- For radio groups, tick exactly one option per group.
- Never act on password, captcha, or login fields.

Screening questions (the applicant is an engineer applying in Australia):
- Right to work / visa / citizenship / permanent residency: answer from work_authorization.
- Security clearance (Baseline, NV1, NV2): from work_authorization.security_clearance; if it's
  not stated, answer that they hold none. "Willing to obtain": yes only if they're a citizen.
- "Do you have experience with X?": yes only if the resume or skills show X. Don't claim
  adjacent tools.
- "Years of experience with X": count from the resume's dates for roles that used X; round
  down; 0 (or the lowest option) if X isn't in the resume.
- Salary: AUD, from compensation (use salary_range_min-salary_range_max for a range). These
  figures are BASE salary, excluding superannuation. If the question asks for a package or
  "including super" (total remuneration), add the 12% super guarantee (e.g. 120000 base ->
  134400 including super). In free text, write e.g. "$120,000 plus super".
- "How did you hear about this job?": the job board in FOUND ON (pick the matching option, e.g.
  "LinkedIn", "Indeed", "Jora", "SEEK"; otherwise "Job board" or "Other: <board>").
- "Were you referred by an employee?": no. "Have you worked for / applied to this company
  before?": no, unless the resume shows that employer.
- Notice period / start date: from availability.
- Relocation: from availability.willing_to_relocate; driver's licence from availability.
- Aboriginal or Torres Strait Islander, gender, disability and other diversity questions: use
  eeo_voluntary values; otherwise pick "Prefer not to say".
- Criminal history, medical conditions, police check / Working with Children Check details, or
  anything legal the profile doesn't answer: DON'T answer. Add a needs_attention note instead.
- Free-text questions ("Why this role?", "Describe a project where..."): 2-4 sentences (or the
  stated word limit), specific to this job: name something from JOB DESCRIPTION (the product,
  system, problem or team) and connect it to real resume work with a concrete detail. Never invent
  facts about the employer beyond what the description says.
- Selection criteria / "address the following" (Australian government, university and council
  forms): one answer per criterion in STAR order (situation, task, action, result) from a real
  resume example, within the stated word limit (otherwise about 150-250 words), first person.
- Every free-text answer must read like the applicant wrote it: plain Australian English, no
  "I am thrilled", no "not just X but Y", no exclamation marks, no buzzwords (leverage, passionate,
  dynamic, synergy, spearheaded).
  Plain and direct, no clichés. Australian English spelling. Never give the applicant a job
  title they haven't held, or tools, numbers or achievements that aren't in the resume.
- Dates: DD/MM/YYYY unless the field's placeholder shows another format.
- Pick option text EXACTLY as it appears in "options".
- Output ONLY the JSON object."""


def _call_gemini(prompt: str, api_key: str, model: str) -> dict:
    """POST to Gemini generateContent and parse the JSON response."""
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
    }
    url = _GEMINI_URL.format(model=model)
    budget.check(model, budget.APPLY)
    with httpx.Client(timeout=_GEMINI_TIMEOUT) as client:
        for attempt in range(_GEMINI_MAX_RETRIES):
            resp = client.post(url, json=payload, headers={"x-goog-api-key": api_key})
            if budget.is_daily_quota_error(resp.status_code, resp.text):
                raise budget.exhausted_error(model)
            if resp.status_code in (429, 500, 503) and attempt < _GEMINI_MAX_RETRIES - 1:
                wait = min(10 * (2 ** attempt), 60)
                logger.warning("Gemini HTTP %s, retrying in %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            if 400 <= resp.status_code < 500:
                # Surface Gemini's own message, e.g. "API key not valid".
                try:
                    detail = resp.json()["error"]["message"]
                except Exception:
                    detail = resp.text[:200]
                raise RuntimeError(f"Gemini error ({resp.status_code}): {detail}")
            resp.raise_for_status()
            break
    budget.record(model)
    data = resp.json()
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return parse_json_response(text)


def parse_json_response(text: str) -> dict:
    """Parse model output, tolerating ```json fences or surrounding prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        data = json.loads(text[start:end + 1])
    if isinstance(data, list):
        data = {"actions": data}
    data.setdefault("actions", [])
    data.setdefault("needs_attention", [])
    return data


def _build_user_prompt(job: dict, fields: list[dict], safe_profile: dict,
                       safe_resume: str, has_cover_pdf: bool, has_cover_text: bool,
                       ats: str | None) -> str:
    sections = [
        f"JOB: {job.get('title', '')} @ {job.get('company') or job.get('site', '')} ({job.get('location', '')})",
        f"FOUND ON: {_BOARD_NAMES.get(str(job.get('site') or '').lower(), job.get('site') or 'a job board')}",
        f"TODAY: {datetime.now():%d/%m/%Y}",
    ]
    if ats:
        sections.append(f"ATS HINT: {_ATS_HINTS[ats]}")
    sections.append(
        "AVAILABLE DOCUMENTS: {{RESUME_PDF}}, {{RESUME_TEXT}}"
        + (", {{COVER_LETTER_PDF}}" if has_cover_pdf else "")
        + (", {{COVER_LETTER_TEXT}}" if has_cover_text else "")
    )
    description = (job.get("full_description") or "").strip()
    if description:  # for "Why this role?" and selection criteria answers specific to this job
        sections.append("JOB DESCRIPTION (excerpt):\n" + description[:_MAX_AD_CHARS])
    sections.append("APPLICANT PROFILE:\n" + json.dumps(safe_profile, indent=1))
    sections.append("RESUME:\n" + safe_resume[:_MAX_RESUME_CHARS])
    sections.append("FORM FIELDS:\n" + json.dumps(fields, indent=0))
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Applying actions
# ---------------------------------------------------------------------------

# Suggestion lists of searchable drop-downs and autocomplete inputs:
# ARIA options (React-Select, Greenhouse), Lever's location results, Google Places
_OPTION_SELECTOR = "[role=option]:visible, .dropdown-results > :visible, .pac-item:visible"
_MAX_OPTIONS = 60


def _visible_options(frame) -> list[str]:
    try:
        return [t.strip() for t in frame.locator(_OPTION_SELECTOR).all_inner_texts() if t.strip()]
    except Exception:
        return []


def _click(loc, timeout_ms: int = 3000) -> None:
    """Click like a person would, even if the element never counts as 'stable'.

    Playwright waits for an element to stop moving before clicking; on a busy
    machine (or a widget that keeps resizing) that wait can time out although
    the element is right there. Then click it at its current position anyway.
    """
    try:
        loc.click(timeout=timeout_ms)
    except Exception as e:
        if "Timeout" not in type(e).__name__ and "Timeout" not in str(e):
            raise
        loc.click(force=True, timeout=timeout_ms)


def _choose(frame, loc, value: str, timeout_ms: int = 4000) -> None:
    """Pick `value` in a searchable drop-down or autocomplete input.

    Types the value to filter the list, waits for suggestions and clicks the
    best match. Never presses Enter: in a plain input that would submit the
    form, which only the user may do.
    """
    for _attempt in range(2):  # a busy page can swallow the first pick: check and retry once
        _click(loc)
        if loc.evaluate("el => ['input', 'textarea'].includes(el.tagName.toLowerCase())"):
            loc.fill(value, timeout=3000)
        best = _click_option(frame, value, timeout_ms)
        if best is None:
            raise RuntimeError(f"no option matching the answer ({len(_visible_options(frame))} shown)")
        frame.page.wait_for_timeout(150)
        shown = _chosen_text(loc)
        if shown is None or answers.best_option([shown], best):
            return
    raise RuntimeError("the drop-down didn't keep the chosen option")


def _click_option(frame, value: str, timeout_ms: int) -> str | None:
    """Wait for a suggestion matching `value` and click it; returns its text."""
    waited = 0
    while waited <= timeout_ms:
        texts = _visible_options(frame)
        best = answers.best_option(texts, value) if texts else None
        if best:
            for option in frame.locator(_OPTION_SELECTOR).all():
                if option.inner_text().strip() == best:
                    _click(option)
                    return best
        frame.page.wait_for_timeout(250)
        waited += 250
    return None


def _chosen_text(loc) -> str | None:
    """What a searchable drop-down now shows (None if it can't be read)."""
    try:
        text = loc.evaluate("""el => {
            const box = el.closest('[class*="container"]');
            const v = box && box.querySelector('[class*="single-value"], [class*="singleValue"]');
            if (v) return v.innerText.trim();  // not el.value: that's only what we typed
            if (el.tagName !== 'INPUT') return null;  // a widget we can't read back
            return (el.value || '').trim();
        }""")
    except Exception:
        return None
    return text or ""


def _collect_combobox_options(fields: list[dict], frame_by_id: dict) -> None:
    """Open each empty searchable drop-down once to read its options."""
    for f in fields:
        if f.get("type") != "combobox" or f.get("options") or (f.get("value") or "").strip():
            continue
        frame = frame_by_id.get(f["id"])
        if frame is None:
            continue
        try:
            _click(frame.locator(f'[data-ap-id="{f["id"]}"]'), 2000)
            frame.page.wait_for_timeout(300)
            texts = _visible_options(frame)
            frame.page.keyboard.press("Escape")  # closes the menu; never submits
        except Exception:
            continue
        if texts:
            f["options"] = texts[:_MAX_OPTIONS]
            if len(texts) > _MAX_OPTIONS:
                f["more_options"] = True  # type to filter: "choose" with any option text


def _apply_action(action: dict, frame_by_id: dict, local_values: dict[str, str],
                  file_values: dict[str, str]) -> str | None:
    """Perform one Gemini-proposed action. Returns a log line, or None if skipped."""
    fid = action.get("id", "")
    kind = action.get("action", "")
    frame = frame_by_id.get(fid)
    if frame is None or kind not in ("fill", "select", "check", "uncheck", "choose", "upload"):
        return None
    loc = frame.locator(f'[data-ap-id="{fid}"]')

    if kind == "upload":
        path = file_values.get(str(action.get("value", "")).strip())
        if not path:
            return None
        loc.set_input_files(path)
        return f"upload {fid} <- {Path(path).name}"

    if kind in ("check", "uncheck"):
        try:
            loc.set_checked(kind == "check", timeout=3000)
        except Exception:
            # Custom-styled inputs are often visually hidden: click the label instead
            loc.set_checked(kind == "check", force=True, timeout=3000)
        return f"{kind} {fid}"

    value = unredact(action.get("value", ""), local_values)
    if value is None:
        logger.info("Skipping %s: unresolved placeholder in %r", fid, action.get("value"))
        return None

    if kind == "select":
        try:
            loc.select_option(label=value, timeout=3000)
        except Exception:
            loc.select_option(value=value, timeout=3000)
    elif kind == "choose":
        _choose(frame, loc, value)
    else:
        loc.fill(value, timeout=5000)
    # Log the field, not the value — the value may be sensitive
    return f"{kind} {fid}"


def _run_actions(actions: list[dict], frame_by_id: dict, ctx: dict, lf) -> list[dict]:
    """Apply actions in order; returns the ones that took effect."""
    done = []
    for action in actions:
        try:
            line = _apply_action(action, frame_by_id, ctx["local_values"], ctx["file_values"])
        except Exception as e:
            line = None
            lines = [ln.strip() for ln in str(e).strip().splitlines() if ln.strip()]
            why = lines[0][:120] + (f" ({lines[-1][:120]})" if len(lines) > 1 else "")  # the last step says why
            lf.write(f"  !! {action.get('action')} {action.get('id')} failed: {why}\n")
        if line:
            done.append(action)
            lf.write(f"  >> {line}\n")
    return done


def _remember_ai_answers(conn, fields: list[dict], done: list[dict], pii_values: set[str]) -> None:
    """Save the AI's answers to short, reusable questions into the answer bank."""
    by_id = {f["id"]: f for f in fields}
    for action in done:
        f = by_id.get(action.get("id"))
        value = str(action.get("value", "")).strip()
        if not f or "{{" in value or value.lower() in pii_values or answers.is_standard(f):
            continue
        if f["type"] == "radio" and action["action"] == "check" and f.get("group"):
            answers.save_answer(conn, f["group"], f.get("label", ""), "ai")
        elif (f["type"] in ("select", "combobox") and action["action"] in ("select", "choose")
              or f["type"] in ("text", "number") and action["action"] == "fill" and len(value) <= 80):
            answers.save_answer(conn, f.get("label", ""), value, "ai")


def _upload_key(page, fields: list[dict], action: dict) -> tuple:
    """An upload box across re-scans and 'Fill this page again' (some sites empty it after uploading)."""
    f = next((f for f in fields if f["id"] == action["id"]), {})
    return (urlsplit(page.url).path, f.get("label"), f.get("name"), f.get("html_id"),
            (f.get("context") or "")[:60], action.get("value"))


def _signature(fields: list[dict], action: dict) -> tuple:
    """Identify a field across re-scans (data-ap-ids are renumbered each scan)."""
    f = next((f for f in fields if f["id"] == action["id"]), {})
    return (f.get("type"), f.get("label"), f.get("group"), f.get("name"), f.get("html_id"), action["action"])


def _known_actions(fields: list[dict], frame_by_id: dict, ats: str | None, ctx: dict,
                   lf) -> tuple[list[dict], list[dict]]:
    """Everything answerable without the AI: site rules, standard fields, saved answers."""
    known, remaining = adapters.site_actions(ats, fields, ctx["local_values"], ctx["file_values"])
    standard, remaining = answers.standard_actions(remaining, ctx["local_values"], ctx["file_values"],
                                                   ctx["profile"])
    known += standard
    _collect_combobox_options(remaining, frame_by_id)
    conn = ctx.get("conn")
    if conn is not None:
        from_bank, remaining, used = answers.bank_actions(conn, remaining)
        known += from_bank
        if used:
            lf.write(f"  answer bank: {len(used)} saved answer(s) reused\n")
    return known, remaining


def _fill_page(page, job: dict, ctx: dict, lf) -> tuple[list[dict], dict, list[str]]:
    """Scan the current page and fill it: standard fields and saved answers
    in code, then Gemini for whatever is left (if anything).

    Returns (controls, frame_by_id, needs_attention).
    """
    fields, controls, frame_by_id = _scan(page)
    lf.write(f"  scanned {len(fields)} fields, {len(controls)} controls @ {page.url}\n")
    if not fields:
        return controls, frame_by_id, []

    ats = detect_ats(page.url) or ctx["ats"]
    tried: set[tuple] = set()
    filled = 0
    unpicked: list[str] = []
    remaining = fields
    # Choices can reveal more fields (SEEK's "Upload a resume" shows the file
    # input, Greenhouse follow-up questions), so look again after each round.
    uploaded = ctx.setdefault("uploaded", set())  # upload boxes already filled in this application
    for _ in range(3):
        known, remaining = _known_actions(fields, frame_by_id, ats, ctx, lf)
        known = [a for a in known if _signature(fields, a) not in tried
                 and not (a["action"] == "upload" and _upload_key(page, fields, a) in uploaded)]
        if not known:
            break
        tried |= {_signature(fields, a) for a in known}
        done = _run_actions(known, frame_by_id, ctx, lf)
        uploaded |= {_upload_key(page, fields, a) for a in done if a["action"] == "upload"}
        filled += len(done)
        by_id = {f["id"]: f for f in fields}
        unpicked += [by_id[a["id"]].get("label") or "a drop-down" for a in known
                     if a not in done and a["action"] == "choose" and a["id"] in by_id]
        fields, controls, frame_by_id = _scan(page)
    else:
        _, remaining = _known_actions(fields, frame_by_id, ats, ctx, lf)  # ids from the last scan
    lf.write(f"  filled {filled} fields without AI\n")
    conn = ctx.get("conn")
    notes = [f"Pick '{label[:60]}' from its list yourself (no suggestion matched)" for label in unpicked]

    # Ticked boxes and already-chosen options need nothing from the AI
    if not any(not (f["type"] == "checkbox" and f.get("checked")) for f in remaining):
        lf.write("  nothing left for Gemini on this page\n")
        return controls, frame_by_id, notes

    remaining = [{**f, "context": redact(f["context"], ctx["local_values"])} if f.get("context") else f
                 for f in remaining]  # the text around an upload box can show your name in a file name
    prompt = _build_user_prompt(
        job, remaining, ctx["safe_profile"], ctx["safe_resume"],
        has_cover_pdf="{{COVER_LETTER_PDF}}" in ctx["file_values"],
        has_cover_text=bool(ctx["local_values"].get("{{COVER_LETTER_TEXT}}")),
        ats=ats,
    )
    result = _call_gemini(prompt, ctx["api_key"], ctx["model"])
    done = _run_actions(result["actions"], frame_by_id, ctx, lf)
    lf.write(f"  applied {len(done)}/{len(result['actions'])} AI actions for {len(remaining)} fields\n")
    if conn is not None:
        try:
            _remember_ai_answers(conn, remaining, done, ctx["pii_values"])
        except Exception as e:  # the bank is a convenience; never fail the application over it
            lf.write(f"  answer bank: {e}\n")
    return controls, frame_by_id, notes + [str(n) for n in result["needs_attention"]]


def _page_answers(page, ctx: dict) -> dict[str, tuple[str, str]]:
    """Reusable answers currently on the page ({} if it's gone)."""
    try:
        if page.is_closed():
            return {}
        fields, _, _ = _scan(page)
    except Exception:
        return {}
    return answers.answers_on_page(fields, ctx["pii_values"])


def _learn_from_user(conn, before: dict, after: dict, lf) -> int:
    """Save answers you typed or changed at the review step (they beat the AI's)."""
    learned = 0
    for key, (question, answer) in after.items():
        if before.get(key, (None, None))[1] != answer:
            learned += answers.save_answer(conn, question, answer, "user")
    if learned:
        lf.write(f"  answer bank: learned {learned} answer(s) from you\n")
    return learned


# The job board's own "go to the application" links: Jora's redirect to the
# employer, SEEK's apply flow, Indeed's "apply on company site" redirect.
_BOARD_APPLY_HREF = re.compile(r"jora\.com/job/rd/|seek\.com\.au/job/\d+/apply|indeed\.com/(applystart|rc/clk)",
                               re.IGNORECASE)
_APPLY_TEXT = re.compile(r"^\s*(quick\s+)?apply\b|\bapply (now|on (the )?(company|employer)('s)? (site|website))\b",
                         re.IGNORECASE)


def _find_apply_link(controls: list[dict]) -> str | None:
    """On a job listing page, return the href of its 'Apply' *link* (navigation only).

    Prefers the board's own apply links, then links whose text starts with
    "Apply"; a link that merely mentions applying ("How to apply", a similar
    job's "Quick apply" further down) is the last resort, and the first such
    link on the page wins, which is the ad's own button on these boards.
    """
    links = [c for c in controls if c["tag"] == "a" and c.get("href", "").startswith("http")]
    for rule in (lambda c: _BOARD_APPLY_HREF.search(c["href"]) and re.search(r"\bapply\b", c["text"], re.I),
                 lambda c: _APPLY_TEXT.search(c["text"]) and not re.search(r"\bhow to\b", c["text"], re.I)):
        match = next((c for c in links if rule(c)), None)
        if match:
            return match["href"]
    return None


# Job-board listing pages (the ad, not the application form). Their only
# inputs are the site's search boxes, so they're never filled: the engine
# follows the listing's Apply link to the employer's form instead.
_LISTING_RE = re.compile(
    r"(jora\.com/job/(?!rd/)|indeed\.com/(viewjob|rc/clk|m/viewjob)|linkedin\.com/jobs/view/"
    r"|seek\.com\.au/job/\d+/?(\?|#|$))",
    re.IGNORECASE,
)
_MAX_LISTING_HOPS = 3


def _is_job_listing(url: str) -> bool:
    return bool(_LISTING_RE.search(url or ""))


_OPEN_FORM_JS = r"""() => [...document.querySelectorAll('[role=dialog], [aria-modal=true], dialog[open]')].some(d => {
  const r = d.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && d.querySelector('input:not([type=hidden]), select, textarea');
})"""


def _form_open_over_page(page) -> bool:
    """An application form is open in a dialog on top of the page (e.g. LinkedIn Easy Apply)."""
    for frame in page.frames:
        try:
            if frame.evaluate(_OPEN_FORM_JS):
                return True
        except Exception:
            continue
    return False


_AUTH_PAGE_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const pw = [...document.querySelectorAll('input[type=password]')].filter(vis);
  if (!pw.length) return null;
  // Sign-in pages often link to "Create account", so only the page's title,
  // headings and buttons (not its links) say which kind it is.
  const heads = [...document.querySelectorAll('h1, h2, h3, button, input[type=submit], [role=button]')].filter(vis)
    .map(e => e.innerText || e.value || '').join(' ');
  const text = (document.title + ' ' + heads).toLowerCase();
  const signUp = pw.length > 1 || (/create (an |your )?account|sign ?up|register/.test(text) &&
                                   !/\bsign ?in\b|\blog ?in\b/.test(text));
  return signUp ? 'create_account' : 'sign_in';
}"""
# More fields than a sign-in/sign-up box has: a real form with an optional
# "create a password" field, which is filled as usual.
_MAX_AUTH_FIELDS = 6
_LOGIN_ID_RE = re.compile(r"e-?mail|user ?name|user ?id|login|sign.?in", re.IGNORECASE)


def _auth_page(page) -> str | None:
    """'sign_in' / 'create_account' when the page is a login or sign-up wall, else None."""
    kind = None
    for frame in page.frames:
        try:
            kind = frame.evaluate(_AUTH_PAGE_JS)
        except Exception:
            continue
        if kind:
            break
    if not kind:
        return None
    fields, _, _ = _scan(page)  # password inputs are never scanned
    return kind if len(fields) <= _MAX_AUTH_FIELDS else None


def _fill_login_email(page, ctx: dict, lf) -> None:
    """Type the user's email into an empty email/username box. Never the password."""
    fields, _, frame_by_id = _scan(page)
    for f in fields:
        text = " ".join(str(f.get(k) or "") for k in ("label", "name", "html_id"))
        if f.get("type") in ("email", "text") and not (f.get("value") or "").strip() and (
                f.get("type") == "email" or _LOGIN_ID_RE.search(text)):
            try:
                _apply_action({"id": f["id"], "action": "fill", "value": "{{EMAIL}}"}, frame_by_id,
                              ctx["local_values"], ctx["file_values"])
                lf.write(f"  sign-in page: filled {f['id']} with your email\n")
            except Exception as e:
                lf.write(f"  sign-in page: couldn't fill {f['id']}: {str(e)[:80]}\n")


def _auth_note(kind: str, page) -> str:
    from applypilot.apply.sites import classify
    site = classify(page.url)["name"]
    if kind == "create_account":
        return (f"{site} wants you to create an account before applying. Your email is filled in: choose a "
                "password and finish signing up (check your email if it asks you to verify), then choose "
                "'Fill this page again'. This browser remembers the account for this employer.")
    return (f"Sign in to {site} to continue (your email is filled in; type your password), then choose "
            "'Fill this page again'. No account yet? Use its 'Create account' link. If it only offers "
            "'Sign in with Google' and Google blocks it here: choose 'Not yet: save for later', sign in "
            "with Setup → Log in to sites (or the job's Log in button), close that window, and apply again.")


def _record_apply_site(job: dict, page, ctx: dict, password: bool = False) -> None:
    """Remember where this job's application really is, for the Apply tab's labels."""
    conn = ctx.get("conn")
    if conn is None or _is_job_listing(page.url) or not str(page.url).startswith("http"):
        return
    from applypilot.apply.sites import classify
    found = classify(page.url, password_field=password)
    try:
        conn.execute("UPDATE jobs SET apply_site = ?, apply_site_name = ?, apply_account = ?, apply_site_url = ?, "
                      "apply_site_checked_at = ? WHERE url = ? AND (apply_site IS NULL OR apply_account != 'yes' "
                      "OR ? = 'yes')", (found["site"], found["name"], found["account"], page.url,
                                        datetime.now().isoformat(), job["url"], found["account"]))
        conn.commit()
    except Exception as e:
        logger.debug("Couldn't record the apply site: %s", e)


def _newest_tab(page):
    """The newest tab this page opened: an Apply button may open the employer's form in a new tab.

    Only tabs the page itself opened count, so a tab you open (to read an
    email code, say) is never filled.
    """
    def opener(t):
        try:
            return t.opener()
        except Exception:
            return None

    tabs = [t for t in page.context.pages if not t.is_closed() and t is not page and opener(t) == page]
    return tabs[-1] if tabs else page


def _leave_listing(page, lf) -> list[str] | None:
    """On a job-board listing, open its Apply link (navigation, not submission).

    Returns None if the page isn't a listing (or was left for the form), else
    a note asking the user to start the application themselves.
    """
    for _ in range(_MAX_LISTING_HOPS):
        if not _is_job_listing(page.url) or _form_open_over_page(page):
            return None
        _, controls, _ = _scan(page)
        href = _find_apply_link(controls)
        if not href:
            return ["This is the job ad, and its Apply button isn't a plain link (e.g. LinkedIn Easy Apply or a "
                    "sign-in). Click Apply in the browser, then choose 'Fill this page again'."]
        lf.write(f"  job ad: following its apply link {href}\n")
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
    return None if not _is_job_listing(page.url) or _form_open_over_page(page) else [
        "Still on a job ad after following its Apply link. Click Apply in the browser, then choose "
        "'Fill this page again'."]


# ---------------------------------------------------------------------------
# Human approval gate
# ---------------------------------------------------------------------------

def _highlight_submit(controls: list[dict], frame_by_id: dict) -> None:
    """Outline every Submit/Apply/Send control in red so the human can find it."""
    submit_ids = [c["id"] for c in controls if classify_control(c["text"]) == "submit"]
    for frame in {frame_by_id[i] for i in submit_ids}:
        try:
            frame.evaluate(_HIGHLIGHT_JS, [i for i in submit_ids if frame_by_id[i] is frame])
        except Exception:
            pass


def _approval_gate(page, needs_attention: list[str], dry_run: bool) -> str:
    """Terminal gate: freeze until the human has reviewed (and maybe submitted).

    This is the default gate; the web UI passes its own with the same
    signature. Returns "applied", "manual" (reviewed, not submitted),
    "declined", or "rescan".
    """
    bar = "=" * 64
    print(f"\n{bar}")
    print("  SAFETY GATE — automation has stopped before Submit.")
    print("  Submit/Apply buttons are outlined in red in the browser.")
    if dry_run:
        print("  (--dry-run: do not submit; just review the filled form.)")
    if needs_attention:
        print("  Needs your attention:")
        for note in needs_attention[:15]:
            print(f"    - {note}")
    print("  Review / edit the form, then click Submit in the browser yourself.")
    if staged_uploads():
        print("  To upload by hand, pick these in the browser's file picker:")
        for path in staged_uploads():
            print(f"    {path}")
    print(bar)

    if not sys.stdin or not sys.stdin.isatty():
        # No terminal to block on: freeze with the Playwright Inspector instead.
        print("  No interactive terminal — opening Playwright Inspector. Press Resume when done.")
        page.pause()
        return "manual"

    while True:
        choice = input(
            "  [Enter] = done, close browser   [r] = re-scan & fill this page   "
            "[x] = never apply to this job\n  > "
        ).strip().lower()
        if choice == "r":
            return "rescan"
        if choice == "x":
            return "declined"
        if choice == "":
            answer = input("  Did you submit the application? [y/N] ").strip().lower()
            return "applied" if answer in ("y", "yes") else "manual"


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

def launch_browser(p):
    """Open a visible browser with the persistent ApplyPilot profile.

    Tries CHROME_PATH, then Playwright's own Chromium, then an installed
    Chrome / Edge, so most Windows and Mac users need no extra download.
    """
    candidates: list[str | None] = []
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        candidates.append(env_path)
    try:
        if Path(p.chromium.executable_path).exists():
            candidates.append(None)  # Playwright-managed Chromium
    except Exception:
        pass
    candidates += [b for b in config.find_system_browsers() if b not in candidates]

    width, height = (int(x) for x in config.DEFAULTS["viewport"].split("x"))
    errors: list[str] = []
    for exe in candidates:
        try:
            return p.chromium.launch_persistent_context(
                str(config.LOCAL_BROWSER_PROFILE_DIR),
                headless=False,
                executable_path=exe,
                viewport={"width": width, "height": height},
                # Playwright turns the sandbox off by default, and Chrome / Edge
                # then warn "unsupported command-line flag: --no-sandbox".
                # Containers (Linux) have no usable sandbox, so only there.
                chromium_sandbox=not sys.platform.startswith("linux"),
            )
        except Exception as e:
            errors.append(f"{exe or 'Playwright Chromium'}: {str(e).strip().splitlines()[0][:150]}")
    detail = ("\n  " + "\n  ".join(errors)) if errors else ""
    raise RuntimeError(
        "No usable browser found. Install Google Chrome or Microsoft Edge, "
        "or use 'Install browser' on the Setup page." + detail
    )


# ---------------------------------------------------------------------------
# Entry point (called from launcher.run_job)
# ---------------------------------------------------------------------------

def _load_documents(job: dict, profile: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Read tailored resume / cover letter text and stage PDFs for upload.

    Returns (text_values, file_values) keyed by {{TOKEN}}.
    """
    text_values: dict[str, str] = {}
    file_values: dict[str, str] = {}

    name_slug = (profile.get("personal", {}).get("full_name") or "Applicant").replace(" ", "_")
    dest_dir = config.UPLOAD_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in staged_uploads():  # only this job's documents, so the wrong one can't be picked
        old.unlink(missing_ok=True)

    # Your own resume / cover letter for this job when you gave one, else the generated ones
    from applypilot.own_docs import documents
    docs = documents(job)
    for kind, name, text_key, file_key in (("resume", "Resume", "{{RESUME_TEXT}}", "{{RESUME_PDF}}"),
                                           ("cover", "Cover_Letter", "{{COVER_LETTER_TEXT}}", "{{COVER_LETTER_PDF}}")):
        doc = docs[kind]
        if doc["text"]:
            text_values[text_key] = doc["text"]
        if doc["file"]:
            upload = dest_dir / f"{name_slug}_{name}{doc['file'].suffix.lower()}"  # .pdf, or .docx if yours is
            shutil.copy(doc["file"], upload)
            file_values[file_key] = str(upload)

    return text_values, file_values


def staged_uploads() -> list[Path]:
    """The resume and cover letter ready for the current application (see config.UPLOAD_DIR)."""
    folder = config.UPLOAD_DIR
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.name.endswith(("_Resume.pdf", "_Cover_Letter.pdf",
                                                      "_Resume.docx", "_Cover_Letter.docx")))


def _call_gate(gate, page, needs: list[str], dry_run: bool, snapshot) -> str:
    """Call a gate, passing `snapshot` to gates that accept it."""
    try:
        params = inspect.signature(gate).parameters
    except (TypeError, ValueError):
        params = {}
    if "snapshot" in params:
        return gate(page, needs, dry_run, snapshot=snapshot)
    return gate(page, needs, dry_run)


def run_job(job: dict, worker_id: int = 0, dry_run: bool = False,
            gate=None) -> tuple[str, int]:
    """Fill one application in a visible local browser and wait for the human.

    ``gate(page, needs_attention, dry_run) -> decision`` blocks until the
    human decides; it defaults to the terminal prompt (_approval_gate).

    Returns (status, duration_ms): 'applied', 'manual' (left for the user to
    submit), or 'failed:<reason>', as consumed by launcher.worker_loop.
    """
    from playwright.sync_api import sync_playwright

    gate = gate or _approval_gate
    start = time.time()
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "failed:no_gemini_api_key", 0

    profile = config.load_profile()
    placeholders = build_placeholders(profile)
    text_values, file_values = _load_documents(job, profile)
    if "{{RESUME_PDF}}" not in file_values:
        return "failed:resume_pdf_missing", 0

    try:
        from applypilot.database import get_connection
        conn = get_connection()
        answers.ensure_table(conn)
    except Exception as e:
        logger.warning("Answer bank unavailable: %s", e)
        conn = None

    ctx = {
        "api_key": api_key,
        "profile": profile,
        "conn": conn,
        "pii_values": {v.strip().lower() for v in placeholders.values() if v.strip()},
        "model": config.get_local_engine_model(),
        "local_values": {**placeholders, **text_values},
        "file_values": file_values,
        "safe_profile": redacted_profile(profile, placeholders),
        "safe_resume": redact(text_values.get("{{RESUME_TEXT}}", ""), placeholders),
        "ats": detect_ats(apply_link(job)),
    }
    target = apply_link(job)

    update_state(worker_id, status="applying", job_title=job["title"],
                 company=job.get("site", ""), score=job.get("fit_score", 0),
                 start_time=start, actions=0, last_action="local: launching browser")
    add_event(f"[W{worker_id}] Local engine ({ctx['model']}): {job['title'][:40]}")
    print(f"\n>>> {job['title']} @ {job.get('site', '')}\n    {target}")

    worker_log = config.LOG_DIR / f"local-worker-{worker_id}.log"
    status = "manual"
    # Line-buffered, so the log shows a run while it's still waiting for the user
    with open(worker_log, "a", encoding="utf-8", buffering=1) as lf, sync_playwright() as p:
        lf.write(f"\n{'=' * 60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {job['title']}\n"
                 f"URL: {target}\nModel: {ctx['model']}\n{'=' * 60}\n")
        context = launch_browser(p)
        page = context.pages[0] if context.pages else context.new_page()
        nav_error = None
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            nav_error = str(e).strip().splitlines()[0][:200]
            lf.write(f"  navigation: {e}\n")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # busy pages never go network-idle; the DOM is already loaded

        try:
            needs: list[str] = []
            last_signature = None
            step = 0
            while True:
                newest = _newest_tab(page)
                if newest is not page:
                    lf.write(f"  switched to the new tab {newest.url}\n")
                    page = newest
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                listing_note = _leave_listing(page, lf)
                auth = None if listing_note else _auth_page(page)
                if auth:
                    # A sign-in / sign-up wall: fill the email only, never the
                    # password, don't ask the AI and don't click anything.
                    lf.write(f"  {auth.replace('_', ' ')} page @ {page.url}\n")
                    _record_apply_site(job, page, ctx, password=True)
                    _fill_login_email(page, ctx, lf)
                    decision = gate(page, [_auth_note(auth, page)], dry_run)
                    if decision == "rescan":
                        continue
                    status = {"applied": "applied", "declined": "failed:user_declined"}.get(decision, "manual")
                    break
                if not listing_note:
                    _record_apply_site(job, page, ctx)
                if listing_note:
                    # Don't type into the job board's search boxes
                    _, controls, frame_by_id = _scan(page)
                    _highlight_submit(controls, frame_by_id)
                    decision = gate(page, listing_note, dry_run)
                    if decision == "rescan":
                        continue
                    status = {"applied": "applied", "declined": "failed:user_declined"}.get(decision, "manual")
                    break
                controls, frame_by_id, needs = _fill_page(page, job, ctx, lf)
                if nav_error:
                    # Don't leave the user looking at a blank page without a reason
                    needs = [f"Couldn't open the job page ({target}): {nav_error}. Open it yourself in "
                             "this browser, then choose 'Fill this page again'."] + needs
                    nav_error = None
                update_state(worker_id, actions=step + 1, last_action=f"local: filled step {step + 1}")

                # Re-scan after filling: conditional fields may have appeared,
                # and ids must match the DOM we are about to click in.
                fields_now, controls, frame_by_id = _scan(page)
                if not fields_now:
                    href = _find_apply_link(controls)
                    if href and step == 0:
                        # Navigating to an apply link is not a submission.
                        lf.write(f"  following apply link {href}\n")
                        page.goto(href, wait_until="domcontentloaded", timeout=60000)
                        step += 1
                        continue

                signature = (page.url, tuple(f["label"] for f in fields_now))
                next_btn = next(
                    (c for c in controls if classify_control(c["text"]) == "next"), None
                )
                if next_btn and step < _MAX_STEPS and signature != last_signature:
                    last_signature = signature
                    lf.write(f"  clicking '{next_btn['text']}'\n")
                    frame_by_id[next_btn["id"]].locator(
                        f'[data-ap-id="{next_btn["id"]}"]'
                    ).click(timeout=_NEXT_CLICK_TIMEOUT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    step += 1
                    continue

                _highlight_submit(controls, frame_by_id)
                before = answers.answers_on_page(fields_now, ctx["pii_values"])
                latest: dict = {}

                def snapshot():
                    # Called by the gate while it waits, so answers survive you
                    # pressing Submit (which navigates away from the form).
                    latest.update(_page_answers(page, ctx))

                decision = _call_gate(gate, page, needs, dry_run, snapshot)
                if decision != "declined" and conn is not None:
                    snapshot()
                    try:
                        _learn_from_user(conn, before, latest, lf)
                    except Exception as e:
                        lf.write(f"  answer bank: {e}\n")
                if decision == "rescan":
                    last_signature = None
                    continue
                status = {"applied": "applied", "declined": "failed:user_declined"}.get(decision, "manual")
                break
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.exception("Local engine error")
            lf.write(f"  ERROR: {e}\n")
            print(f"  Local engine error: {e}\n  The browser stays open for manual completion.")
            try:
                decision = gate(page, [f"Automation error: {str(e)[:120]}"], dry_run)
                status = {"applied": "applied", "declined": "failed:user_declined"}.get(decision, "manual")
            except Exception:
                status = f"failed:{str(e)[:100]}"
        finally:
            lf.write(f"  RESULT: {status}\n")
            try:
                context.close()
            except Exception:
                pass

    duration_ms = int((time.time() - start) * 1000)
    update_state(worker_id, status=status.split(":")[0], last_action=f"local: {status}")
    add_event(f"[W{worker_id}] {status.upper()} (local): {job['title'][:30]}")
    return status, duration_ms

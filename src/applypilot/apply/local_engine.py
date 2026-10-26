"""Free local apply engine: Playwright + Gemini (BYOK), human-submitted.

Selected with EXECUTION_ENGINE=local_playwright. Instead of spawning the paid
Claude Code CLI, this engine:

  1. Launches a visible local Chromium (headless=False) with a persistent
     profile, so logins to SEEK / Workday / etc. survive between runs.
  2. Opens the job's application URL and extracts a compact description of
     every form control (label, type, options, current value) from the DOM,
     including inside iframes (Greenhouse embeds).
  3. Sends that field list, plus a *redacted* profile and resume, to Gemini
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

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from applypilot import config
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
  const labelOf = el => {
    let t = '';
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) t = l.innerText;
    }
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map(i => byId(i)?.innerText || '').join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) t = el.getAttribute('placeholder') || '';
    if (!t) {
      let p = el.parentElement;
      for (let i = 0; i < 3 && p && !clean(t); i++) { t = p.innerText; p = p.parentElement; }
    }
    return clean(t);
  };
  const groupOf = el => {
    const fs = el.closest('fieldset');
    const lg = fs && fs.querySelector('legend');
    if (lg) return clean(lg.innerText);
    const rg = el.closest('[role=radiogroup],[role=group]');
    if (rg) return clean(rg.getAttribute('aria-label') ||
                         byId(rg.getAttribute('aria-labelledby'))?.innerText || '');
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const q = p.querySelector('label:not(:has(input)), legend, h3, h4, p, span');
      if (q && !q.contains(el) && clean(q.innerText)) return clean(q.innerText);
    }
    return '';
  };

  // Drop ids from a previous scan so hidden/stale elements can't collide
  for (const el of document.querySelectorAll('[data-ap-id]')) el.removeAttribute('data-ap-id');

  const fields = [];
  const skip = new Set(['hidden', 'submit', 'button', 'image', 'reset', 'password', 'search']);
  let n = 0;
  const sel = 'input, select, textarea, [role=combobox]:not(input), [contenteditable=true]';
  for (const el of document.querySelectorAll(sel)) {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.type || 'text').toLowerCase() : tag;
    if (skip.has(type) || el.disabled || el.readOnly || !visible(el)) continue;
    const id = `${prefix}-${n++}`;
    el.setAttribute('data-ap-id', id);
    const f = { id, type, label: labelOf(el), name: el.name || '', required: !!(el.required ||
                el.getAttribute('aria-required') === 'true') };
    if (tag === 'select') {
      f.options = [...el.options].map(o => clean(o.text)).filter(Boolean).slice(0, 60);
      f.value = clean(el.selectedOptions[0]?.text || '');
    } else if (type === 'checkbox' || type === 'radio') {
      f.group = groupOf(el);
      f.checked = el.checked;
    } else if (type === 'file') {
      f.accept = el.accept || '';
      f.has_file = el.files && el.files.length > 0;
    } else if (el.getAttribute('role') === 'combobox' && tag !== 'input') {
      f.type = 'combobox';
      f.value = clean(el.innerText);
    } else {
      f.value = clean(el.value || el.innerText || '');
    }
    fields.push(f);
  }

  const controls = [];
  const csel = 'button, input[type=submit], input[type=button], a[role=button], [role=button], a';
  let m = 0;
  for (const el of document.querySelectorAll(csel)) {
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
You receive: the applicant profile, their resume, job info, and a JSON list of
form fields extracted from the page (id, type, label, options, current value).

Return JSON: {"actions": [{"id": "...", "action": "...", "value": "..."}],
              "needs_attention": ["short note per required field you could not answer"]}

Actions:
- "fill":   text/email/tel/url/number/date/textarea/contenteditable -> value is the text
- "select": native <select> -> value is the EXACT option text from "options"
- "check":  checkbox or radio to tick -> value ""  (only list ones that must be ON)
- "uncheck": checkbox to clear -> value ""
- "choose": custom combobox -> value is the option text to pick
- "upload": file input -> value "{{RESUME_PDF}}" or "{{COVER_LETTER_PDF}}"

Rules:
- Personal identifiers appear as {{TOKENS}}. Use the tokens VERBATIM as values
  (e.g. "{{EMAIL}}", "{{FIRST_NAME}}"). Never invent or guess real values.
- For a "paste your resume" box use "{{RESUME_TEXT}}"; for a cover letter box use
  "{{COVER_LETTER_TEXT}}".
- Only use facts from the profile/resume. Never fabricate experience.
- Skip fields that already hold a correct value, search boxes, and anything unrelated
  to the application (newsletters, marketing opt-ins: leave unticked).
- For radio groups, tick exactly one option per group.
- Never act on password, captcha, or login fields.
- Location/eligibility questions: answer from profile work_authorization and location.
- Salary: use profile compensation in the posting's currency if stated.
- EEO / voluntary disclosure: use eeo_voluntary values; pick the closest option.
- Output ONLY the JSON object."""


def _call_gemini(prompt: str, api_key: str, model: str) -> dict:
    """POST to Gemini generateContent and parse the JSON response."""
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
    }
    url = _GEMINI_URL.format(model=model)
    with httpx.Client(timeout=_GEMINI_TIMEOUT) as client:
        for attempt in range(_GEMINI_MAX_RETRIES):
            resp = client.post(url, json=payload, headers={"x-goog-api-key": api_key})
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
        f"JOB: {job.get('title', '')} @ {job.get('site', '')} ({job.get('location', '')})",
    ]
    if ats:
        sections.append(f"ATS HINT: {_ATS_HINTS[ats]}")
    sections.append(
        "AVAILABLE DOCUMENTS: {{RESUME_PDF}}, {{RESUME_TEXT}}"
        + (", {{COVER_LETTER_PDF}}" if has_cover_pdf else "")
        + (", {{COVER_LETTER_TEXT}}" if has_cover_text else "")
    )
    sections.append("APPLICANT PROFILE:\n" + json.dumps(safe_profile, indent=1))
    sections.append("RESUME:\n" + safe_resume[:_MAX_RESUME_CHARS])
    sections.append("FORM FIELDS:\n" + json.dumps(fields, indent=0))
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Applying actions
# ---------------------------------------------------------------------------

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
        loc.click(timeout=3000)
        page = frame.page
        option = page.get_by_role("option", name=value, exact=False).first
        try:
            option.click(timeout=3000)
        except Exception:
            page.keyboard.type(value)
            page.keyboard.press("Enter")
    else:
        loc.fill(value, timeout=5000)
    # Log the field, not the value — the value may be sensitive
    return f"{kind} {fid}"


def _fill_page(page, job: dict, ctx: dict, lf) -> tuple[list[dict], dict, list[str]]:
    """Scan the current page, ask Gemini for actions, apply them.

    Returns (controls, frame_by_id, needs_attention).
    """
    fields, controls, frame_by_id = _scan(page)
    lf.write(f"  scanned {len(fields)} fields, {len(controls)} controls @ {page.url}\n")
    if not fields:
        return controls, frame_by_id, []

    prompt = _build_user_prompt(
        job, fields, ctx["safe_profile"], ctx["safe_resume"],
        has_cover_pdf="{{COVER_LETTER_PDF}}" in ctx["file_values"],
        has_cover_text=bool(ctx["local_values"].get("{{COVER_LETTER_TEXT}}")),
        ats=detect_ats(page.url) or ctx["ats"],
    )
    result = _call_gemini(prompt, ctx["api_key"], ctx["model"])
    done = 0
    for action in result["actions"]:
        try:
            line = _apply_action(action, frame_by_id, ctx["local_values"], ctx["file_values"])
        except Exception as e:
            line = None
            lf.write(f"  !! {action.get('action')} {action.get('id')} failed: {str(e)[:120]}\n")
        if line:
            done += 1
            lf.write(f"  >> {line}\n")
    lf.write(f"  applied {done}/{len(result['actions'])} actions\n")
    return controls, frame_by_id, [str(n) for n in result["needs_attention"]]


def _find_apply_link(controls: list[dict]) -> str | None:
    """On a job listing page, return the href of an 'Apply' *link* (navigation only)."""
    for c in controls:
        if c["tag"] == "a" and c.get("href", "").startswith("http") and re.search(
            r"\bapply\b", c["text"], re.IGNORECASE
        ):
            return c["href"]
    return None


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
    dest_dir = config.APPLY_WORKER_DIR / "local"
    dest_dir.mkdir(parents=True, exist_ok=True)

    resume_path = job.get("tailored_resume_path")
    if resume_path:
        base = Path(resume_path)
        for suffix in (".md", ".txt"):
            if base.with_suffix(suffix).exists():
                text_values["{{RESUME_TEXT}}"] = base.with_suffix(suffix).read_text(encoding="utf-8")
                break
        pdf = base.with_suffix(".pdf")
        if pdf.exists():
            upload = dest_dir / f"{name_slug}_Resume.pdf"
            shutil.copy(pdf, upload)
            file_values["{{RESUME_PDF}}"] = str(upload)

    cl_path = job.get("cover_letter_path")
    if cl_path:
        base = Path(cl_path)
        for suffix in (".md", ".txt"):
            if base.with_suffix(suffix).exists():
                text_values["{{COVER_LETTER_TEXT}}"] = base.with_suffix(suffix).read_text(encoding="utf-8")
                break
        pdf = base.with_suffix(".pdf")
        if pdf.exists():
            upload = dest_dir / f"{name_slug}_Cover_Letter.pdf"
            shutil.copy(pdf, upload)
            file_values["{{COVER_LETTER_PDF}}"] = str(upload)

    return text_values, file_values


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

    ctx = {
        "api_key": api_key,
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
    with open(worker_log, "a", encoding="utf-8") as lf, sync_playwright() as p:
        lf.write(f"\n{'=' * 60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {job['title']}\n"
                 f"URL: {target}\nModel: {ctx['model']}\n{'=' * 60}\n")
        context = launch_browser(p)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            lf.write(f"  navigation: {e}\n")

        try:
            needs: list[str] = []
            last_signature = None
            step = 0
            while True:
                controls, frame_by_id, needs = _fill_page(page, job, ctx, lf)
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
                decision = gate(page, needs, dry_run)
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

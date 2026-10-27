"""Form answers that don't need the AI: standard fields and the answer bank.

Standard fields (name, email, phone, links, address, document uploads) are
matched by their label and filled straight from the profile. Screening
questions answered before -- by you or by the AI -- come from the answer
bank (the `answers` table). Only what's left goes to Gemini, and a page
with nothing left costs no AI request at all.

The bank learns from you: when an application reaches the review step,
answers you typed or changed are saved with source "user", and those
always win over the AI's.
"""

from __future__ import annotations

import re
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from applypilot.config import AU_STATES

# Field types whose answers are short and reusable across applications.
# Textareas ("Why do you want this job?") are job-specific and never reused.
_REUSABLE_TEXT_TYPES = {"text", "number"}
_MAX_REUSABLE_TEXT = 80


def question_key(text: str | None) -> str:
    """Normalise a question label so the same question matches across sites."""
    text = re.sub(r"['\u2019]", "", (text or "").lower())  # driver's == drivers
    text = re.sub(r"\(\s*(required|optional)\s*\)|\*", " ", text)
    return " ".join(re.findall(r"[a-z0-9$%]+", text))[:200]


def _norm(text: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def best_option(options: list[str], value: str) -> str | None:
    """The option text matching `value`: exact, state name/abbreviation, then prefix."""
    if not value:
        return None
    wanted = {_norm(value)}
    for abbr, full in AU_STATES.items():
        if _norm(value) in (abbr, _norm(full)):
            wanted |= {abbr, _norm(full)}
    normed = [(o, _norm(o)) for o in options if _norm(o)]
    for option, n in normed:
        if n in wanted:
            return option
    for option, n in normed:
        if any(n.startswith(w) or w.startswith(n) for w in wanted if len(w) > 2 and len(n) > 2):
            return option
    return None


# -- Standard fields --------------------------------------------------------

# (pattern on the label/name, placeholder token or profile.personal field)
_STANDARD_TEXT = [
    (r"\b(first|given) ?name\b|^fname$", "{{FIRST_NAME}}"),
    (r"\b(last|family) ?name\b|\bsurname\b|^lname$", "{{LAST_NAME}}"),
    (r"\bpreferred name\b", "{{PREFERRED_NAME}}"),
    (r"^(your |full |legal )?name$", "{{FULL_NAME}}"),
    (r"\be-?mail\b", "{{EMAIL}}"),
    (r"\b(phone|mobile|cell)\b", "{{PHONE}}"),
    (r"linkedin", "{{LINKEDIN_URL}}"),
    (r"github", "{{GITHUB_URL}}"),
    (r"\bportfolio\b", "{{PORTFOLIO_URL}}"),
    (r"\b(personal )?website\b", "{{WEBSITE_URL}}"),
    (r"\b(post ?code|postal code|zip)\b", "{{POSTAL_CODE}}"),
    (r"\b(street|address line 1|home address|^address$)", "{{STREET_ADDRESS}}"),
    (r"\b(city|suburb|town)\b|^(current )?location( city)?$|^candidate location$", "personal.city"),
    (r"^(state|province|territory|state or territory)$", "personal.province_state"),
    (r"^country( of residence)?$", "personal.country"),
    (r"^(current|most recent) (company|employer)$|^org$", "experience.current_company"),
    (r"^(current|most recent) (job )?title$", "experience.current_job_title"),
]
_MAX_STANDARD_LABEL = 60  # longer labels are questions, not contact fields


def _field_text(field: dict) -> str:
    return " ".join(re.findall(r"[a-z0-9-]+", f"{field.get('label', '')}".lower()))


def _field_keys(field: dict) -> list[str]:
    """The field's name and HTML id, as words ("first_name" -> "first name")."""
    keys = []
    for raw in (field.get("name"), field.get("html_id")):
        words = " ".join(re.findall(r"[a-z0-9]+", (raw or "").lower()))
        if words and not re.search(r"\b[0-9a-f]{8}\b", words):  # skip generated ids (Lever cards)
            keys.append(words)
    return keys


def _standard_token(field: dict) -> str | None:
    label = _field_text(field)
    for pattern, token in _STANDARD_TEXT:
        if len(label) <= _MAX_STANDARD_LABEL and re.search(pattern, label):
            return token
        if any(re.search(pattern, k) for k in _field_keys(field)):
            return token
    return None


def is_standard(field: dict) -> bool:
    """True for contact/address fields that are always filled from the profile."""
    return field.get("type") in ("email", "tel") or _standard_token(field) is not None


def _standard_value(field: dict, local_values: dict[str, str], profile: dict) -> tuple[str, str] | None:
    """(action value, real value) for a standard contact/address field, else None."""
    if field.get("type") == "email":
        token = "{{EMAIL}}"
    elif field.get("type") == "tel":
        token = "{{PHONE}}"
    else:
        token = _standard_token(field)
        if token is None:
            return None
    if token == "{{PHONE}}" and field.get("intl_phone") and local_values.get("{{PHONE_INTL}}"):
        token = "{{PHONE_INTL}}"  # intl-tel-input picks the country from the +61 prefix
    if token.startswith("{{"):
        real = local_values.get(token)
        return (token, real) if real else None
    section, _, key = token.partition(".")
    real = str((profile.get(section) or {}).get(key) or "")
    if not real and key == "current_job_title":
        real = str((profile.get(section) or {}).get("current_title") or "")
    return (real, real) if real else None


_COVER_RE = re.compile(r"cover")
_RESUME_RE = re.compile(r"r[eé]sum[eé]|\bcv\b|curriculum")


def _upload_kind(text: str) -> str | None:
    cover, resume = bool(_COVER_RE.search(text)), bool(_RESUME_RE.search(text))
    return "cover" if cover and not resume else "resume" if resume and not cover else None


def _upload_actions(boxes: list[dict], file_values: dict[str, str]) -> dict[str, str | None]:
    """Which document goes in each upload box: {field id: token, or None to leave it}.

    Boxes missing from the result are left for the AI. The box's own label
    decides; failing that, the text of its row or section ("context"). A box
    whose row already shows one of our files was uploaded already. The resume
    never goes in two boxes: with two boxes and one clearly the resume, the
    other gets the cover letter.
    """
    names = {token: Path(path).name.lower() for token, path in file_values.items()}
    result: dict[str, str | None] = {}
    undecided: list[dict] = []
    for f in boxes:
        context = (f.get("context") or "").lower()
        if f.get("has_file") or any(name and name in context for name in names.values()):
            result[f["id"]] = None  # already holds a file
            continue
        label = " ".join([_field_text(f), *_field_keys(f)])
        kind = (("cover" if _COVER_RE.search(label) else "resume" if _RESUME_RE.search(label) else None)
                or _upload_kind(context))
        if kind == "cover" and "{{COVER_LETTER_PDF}}" in file_values:
            result[f["id"]] = "{{COVER_LETTER_PDF}}"
        elif kind == "resume" and "{{RESUME_PDF}}" in file_values:
            result[f["id"]] = "{{RESUME_PDF}}"
        elif kind == "cover":
            result[f["id"]] = None  # no cover letter PDF: the AI can't help either
        else:
            undecided.append(f)
    if len(boxes) == 1 and undecided and "{{RESUME_PDF}}" in file_values:
        result[undecided[0]["id"]] = "{{RESUME_PDF}}"  # the only upload box on the page is for the resume
        undecided = []
    resumes = [i for i, t in result.items() if t == "{{RESUME_PDF}}"]
    for extra in resumes[1:]:  # never the resume twice: keep the first, leave the rest to the AI
        result.pop(extra)
    resumes = resumes[:1]
    has_cover = "{{COVER_LETTER_PDF}}" in result.values()
    if len(boxes) == 2 and len(undecided) == 1 and resumes and not has_cover and "{{COVER_LETTER_PDF}}" in file_values:
        result[undecided[0]["id"]] = "{{COVER_LETTER_PDF}}"
    return result


def standard_actions(fields: list[dict], local_values: dict[str, str], file_values: dict[str, str],
                     profile: dict) -> tuple[list[dict], list[dict]]:
    """Actions for standard fields, and the fields still needing an answer.

    Fields that already have a value are skipped entirely (left as they are
    and not sent to the AI); so are radio groups that already have a choice.
    """
    actions: list[dict] = []
    remaining: list[dict] = []
    answered_groups = {question_key(f.get("group")) for f in fields
                       if f.get("type") == "radio" and f.get("checked")}
    uploads = _upload_actions([f for f in fields if f.get("type") == "file"], file_values)
    for f in fields:
        ftype = f.get("type")
        if ftype == "file":
            if f["id"] in uploads:
                if uploads[f["id"]]:
                    actions.append({"id": f["id"], "action": "upload", "value": uploads[f["id"]]})
            else:
                remaining.append(f)
            continue
        if ftype == "radio":
            if question_key(f.get("group")) not in answered_groups:
                remaining.append(f)
            continue
        if ftype == "checkbox":
            remaining.append(f)
            continue
        if has_value(f):
            continue  # already filled (by the site, by you, or on an earlier pass)
        if (ftype == "textarea" and "{{COVER_LETTER_TEXT}}" in local_values
                and re.search(r"cover", _field_text(f)) and len(_field_text(f)) <= _MAX_STANDARD_LABEL):
            actions.append({"id": f["id"], "action": "fill", "value": "{{COVER_LETTER_TEXT}}"})
            continue
        standard = _standard_value(f, local_values, profile)
        if standard is None:
            if not f.get("required") and _standard_token(f):
                continue  # e.g. an optional Portfolio URL you don't have: the AI can't know it either
            remaining.append(f)
        elif ftype == "select":
            option = best_option(f.get("options") or [], standard[1])
            if option:
                actions.append({"id": f["id"], "action": "select", "value": option})
            else:
                remaining.append(f)
        elif ftype == "combobox" or token_is_location(f):
            # Searchable list: type the answer and pick the matching suggestion
            actions.append({"id": f["id"], "action": "choose", "value": standard[0]})
        else:
            actions.append({"id": f["id"], "action": "fill", "value": standard[0]})
    return actions, remaining


_PLACEHOLDER_OPTION = re.compile(r"^(select|choose|please)\b|^-+$|^none selected$")


def has_value(field: dict) -> bool:
    """Whether the field already holds an answer (a drop-down still on "Select..." doesn't)."""
    value = (field.get("value") or "").strip()
    if field.get("type") in ("select", "combobox") and _PLACEHOLDER_OPTION.search(value.lower()):
        return False
    return bool(value)


def token_is_location(field: dict) -> bool:
    """Autocomplete location inputs (Lever's "Current location") need a suggestion picked."""
    return _standard_token(field) == "personal.city" and "location" in " ".join(
        [_field_text(field), *_field_keys(field)])


# -- Answer bank --------------------------------------------------------------

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            question_key TEXT PRIMARY KEY,
            question     TEXT,
            answer       TEXT NOT NULL,
            source       TEXT NOT NULL,      -- 'user' (you) or 'ai'
            uses         INTEGER DEFAULT 0,
            updated_at   TEXT
        )
    """)
    conn.commit()


def save_answer(conn: sqlite3.Connection, question: str, answer: str, source: str) -> bool:
    """Store an answer. An AI answer never replaces one you gave. Returns True if stored."""
    key, answer = question_key(question), (answer or "").strip()
    if not key or not answer:
        return False
    existing = conn.execute("SELECT answer, source FROM answers WHERE question_key = ?", (key,)).fetchone()
    if existing and existing[1] == "user" and source != "user":
        return False
    if existing and existing[0] == answer and existing[1] == source:
        return False
    conn.execute(
        "INSERT INTO answers (question_key, question, answer, source, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(question_key) DO UPDATE SET question = excluded.question, answer = excluded.answer, "
        "source = excluded.source, updated_at = excluded.updated_at",
        (key, question.strip()[:300], answer, source, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return True


def lookup(conn: sqlite3.Connection, question: str) -> str | None:
    row = conn.execute("SELECT answer FROM answers WHERE question_key = ?", (question_key(question),)).fetchone()
    return row[0] if row else None


def _reusable_text(field: dict) -> bool:
    return field.get("type") in _REUSABLE_TEXT_TYPES


def bank_actions(conn: sqlite3.Connection, fields: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Answer remaining fields from the bank.

    Returns (actions, still_remaining, keys_used).
    """
    actions: list[dict] = []
    remaining: list[dict] = []
    used: list[str] = []
    radios: dict[str, list[dict]] = {}
    for f in fields:
        if f.get("type") == "radio":
            radios.setdefault(question_key(f.get("group")), []).append(f)
            continue
        answer = (lookup(conn, f.get("label"))
                  if f.get("type") in ("select", "combobox", *_REUSABLE_TEXT_TYPES) else None)
        if answer and f.get("type") in ("select", "combobox"):
            option = best_option(f.get("options") or [], answer)
            if option or (f.get("type") == "combobox" and f.get("more_options")):
                kind = "select" if f["type"] == "select" else "choose"
                actions.append({"id": f["id"], "action": kind, "value": option or answer})
                used.append(question_key(f.get("label")))
                continue
        elif answer and _reusable_text(f):
            actions.append({"id": f["id"], "action": "fill", "value": answer})
            used.append(question_key(f.get("label")))
            continue
        remaining.append(f)
    for key, group in radios.items():
        answer = lookup(conn, group[0].get("group")) if key else None
        option = best_option([r.get("label", "") for r in group], answer) if answer else None
        chosen = next((r for r in group if r.get("label") == option), None) if option else None
        if chosen:
            actions.append({"id": chosen["id"], "action": "check", "value": ""})
            used.append(key)
        else:
            remaining.extend(group)
    if used:
        conn.executemany("UPDATE answers SET uses = uses + 1 WHERE question_key = ?", [(k,) for k in used])
        conn.commit()
    return actions, remaining, used


def answers_on_page(fields: list[dict], pii_values: set[str]) -> dict[str, tuple[str, str]]:
    """{question_key: (question, current answer)} for reusable questions on a page.

    Skips anything holding one of your personal details (those come from
    the profile, never the bank) and long free text.
    """
    found: dict[str, tuple[str, str]] = {}
    for f in fields:
        ftype = f.get("type")
        if ftype == "radio":
            if f.get("checked") and f.get("group"):
                found[question_key(f["group"])] = (f["group"], f.get("label", ""))
            continue
        if ftype in ("select", "combobox") or _reusable_text(f):
            value = (f.get("value") or "").strip()
            if not value or len(value) > _MAX_REUSABLE_TEXT or not f.get("label") or is_standard(f):
                continue
            if value.lower() in pii_values:
                continue
            if not has_value(f):
                continue  # still on the placeholder option
            found[question_key(f["label"])] = (f["label"], value)
    return {k: v for k, v in found.items() if k}

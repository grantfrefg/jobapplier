"""Site adapters: fixed answers for the predictable parts of known job sites.

Generic label matching (answers.standard_actions) already handles the
contact fields of most forms. These rules cover what a label can't tell:

- SEEK: the "choose documents" step picks how to send the resume and cover
  letter with radio buttons ("Upload a resume", "Write a cover letter"...).
- Lever: there's often no cover letter field, only "Additional
  information"; the cover letter goes there.
- Greenhouse: fields are identified by stable HTML ids (first_name,
  resume, cover_letter, candidate-location...), handled by the generic
  matcher once ids are scanned; nothing extra is needed here today.

Each adapter returns (actions, remaining) like standard_actions, and only
ever fills fields or picks options. Navigation and the never-click-Submit
rule stay in local_engine.
"""

from __future__ import annotations

import re
import unicodedata


def _norm(text: str | None) -> str:
    """Lower-case words without accents ("Resumé" -> "resume", "Don't" -> "don t")."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", plain.lower()))


def _radio_groups(fields: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for f in fields:
        if f.get("type") == "radio":
            groups.setdefault(f.get("group") or f.get("name") or "", []).append(f)
    return groups


def _pick(group: list[dict], *wanted: str) -> dict | None:
    """First option whose label starts with one of the wanted phrases, in order."""
    for phrase in wanted:
        for f in group:
            if _norm(f.get("label")).startswith(phrase):
                return f
    return None


def seek_actions(fields: list[dict], local_values: dict, file_values: dict) -> tuple[list[dict], list[dict]]:
    """SEEK's document step: upload the tailored resume, include the cover letter."""
    actions: list[dict] = []
    handled: set[str] = set()
    for group in _radio_groups(fields).values():
        labels = " | ".join(_norm(f.get("label")) for f in group)
        if any(f.get("checked") for f in group):
            chosen = next(f for f in group if f.get("checked"))
            if not _norm(chosen.get("label")).startswith(("don t include", "dont include", "do not include")):
                handled |= {f["id"] for f in group}
                continue
        choice = None
        if "upload a resume" in labels:
            choice = _pick(group, "upload a resume", "upload resume")
        elif "cover letter" in labels:
            if "{{COVER_LETTER_PDF}}" in file_values:
                choice = _pick(group, "upload a cover letter", "upload cover letter")
            if choice is None and local_values.get("{{COVER_LETTER_TEXT}}"):
                choice = _pick(group, "write a cover letter", "write cover letter")
        if choice:
            actions.append({"id": choice["id"], "action": "check", "value": ""})
            handled |= {f["id"] for f in group}
    return actions, [f for f in fields if f["id"] not in handled]


def lever_actions(fields: list[dict], local_values: dict, file_values: dict) -> tuple[list[dict], list[dict]]:
    """Lever: the cover letter goes in "Additional information" when there's no other place."""
    has_cover_field = any("cover" in _norm(f.get("label")) for f in fields)
    actions: list[dict] = []
    remaining: list[dict] = []
    for f in fields:
        if (f.get("name") == "comments" and f.get("type") == "textarea" and not has_cover_field
                and not (f.get("value") or "").strip() and local_values.get("{{COVER_LETTER_TEXT}}")):
            actions.append({"id": f["id"], "action": "fill", "value": "{{COVER_LETTER_TEXT}}"})
        else:
            remaining.append(f)
    return actions, remaining


ADAPTERS = {"seek": seek_actions, "lever": lever_actions}


def site_actions(ats: str | None, fields: list[dict], local_values: dict,
                 file_values: dict) -> tuple[list[dict], list[dict]]:
    """The adapter's actions for this site (none for sites without one)."""
    adapter = ADAPTERS.get(ats or "")
    if adapter is None:
        return [], fields
    return adapter(fields, local_values, file_values)

"""Fixed answers for profile fields that only have a few sensible values.

The Setup form shows these as drop-downs instead of free text, so the
wording is always what the rest of the app looks for (work rights for the
resume header and the ATS check, Yes/No for form answers, the security
clearance levels for scoring). Older or hand-edited profiles are mapped onto
these values when loaded (true -> Yes, "Masters" -> "Master's degree").
"""

from __future__ import annotations

import re

YES_NO = ["Yes", "No"]

# "section.key" -> allowed values. "" means "none / not set" and is shown as the label in EMPTY_LABELS.
OPTIONS: dict[str, list[str]] = {
    "personal.province_state": ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"],
    "personal.country": ["Australia", "New Zealand", "United Kingdom", "United States", "Canada"],
    "work_authorization.legally_authorized_to_work": YES_NO,
    "work_authorization.require_sponsorship": YES_NO,
    "work_authorization.work_permit_type": [
        "", "Australian citizen", "Permanent resident", "New Zealand citizen",
        "Temporary visa with full work rights", "Temporary visa with limited work rights",
        "Student visa", "Working holiday visa",
    ],
    "work_authorization.security_clearance": ["", "Baseline", "NV1", "NV2", "PV"],
    "availability.available_for_full_time": YES_NO,
    "availability.available_for_contract": YES_NO,
    "availability.willing_to_relocate": YES_NO,
    "availability.drivers_licence": YES_NO,
    "availability.notice_period": ["Immediately", "1 week", "2 weeks", "3 weeks", "4 weeks", "6 weeks",
                                   "8 weeks", "3 months"],
    "compensation.salary_currency": ["AUD", "NZD", "GBP", "USD", "CAD"],
    "experience.education_level": [
        "Year 12", "Certificate or diploma", "Bachelor's degree", "Honours degree",
        "Graduate certificate or diploma", "Master's degree", "PhD",
    ],
    "eeo_voluntary.gender": ["Prefer not to say", "Woman", "Man", "Non-binary"],
    "eeo_voluntary.aboriginal_or_torres_strait_islander": [
        "Prefer not to say", "No", "Aboriginal", "Torres Strait Islander", "Aboriginal and Torres Strait Islander",
    ],
    "eeo_voluntary.disability_status": ["Prefer not to say", "No", "Yes"],
    "eeo_voluntary.veteran_status": ["Prefer not to say", "No", "Yes (Australian Defence Force)"],
}

EMPTY_LABELS = {
    "work_authorization.work_permit_type": "Not set",
    "work_authorization.security_clearance": "None",
}

# Whole numbers (shown as number boxes)
NUMBERS = {"experience.years_of_experience_total", "compensation.salary_expectation",
           "compensation.salary_range_min", "compensation.salary_range_max"}

# Older wording and wizard answers -> the option they mean
_ALIASES = {
    "true": "Yes", "false": "No", "y": "Yes", "n": "No",
    "decline to self-identify": "Prefer not to say", "i do not wish to answer": "Prefer not to say",
    "i am not a protected veteran": "No", "prefer not to answer": "Prefer not to say",
    "citizen": "Australian citizen", "australian citizen": "Australian citizen", "pr": "Permanent resident",
    "permanent resident": "Permanent resident", "none": "", "no clearance": "",
    "masters": "Master's degree", "master's": "Master's degree", "master": "Master's degree",
    "bachelors": "Bachelor's degree", "bachelor's": "Bachelor's degree", "bachelor": "Bachelor's degree",
    "bachelor's degree": "Bachelor's degree", "honours": "Honours degree", "phd": "PhD", "doctorate": "PhD",
    "high school": "Year 12", "diploma": "Certificate or diploma", "immediate": "Immediately",
    "new south wales": "NSW", "victoria": "VIC", "queensland": "QLD", "western australia": "WA",
    "south australia": "SA", "tasmania": "TAS", "australian capital territory": "ACT",
    "northern territory": "NT", "usa": "United States", "uk": "United Kingdom",
}


def normalise(path: str, value):
    """The option an older or hand-typed value means, or the value unchanged if none fits."""
    options = OPTIONS.get(path)
    if options is None:
        return value
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value if value is not None else "").strip()
    for option in options:
        if text.lower() == option.lower():
            return option
    key = re.sub(r"\s+", " ", text.lower())
    if key in _ALIASES and _ALIASES[key] in options:
        return _ALIASES[key]
    return value


def normalise_profile(profile: dict) -> dict:
    out = {}
    for section, fields in (profile or {}).items():
        if isinstance(fields, dict):
            out[section] = {k: normalise(f"{section}.{k}", v) for k, v in fields.items()}
        else:
            out[section] = fields
    return out


def invalid_choices(profile: dict) -> list[str]:
    """Fields whose value isn't one of their options (after normalising)."""
    bad = []
    for path, options in OPTIONS.items():
        section, key = path.split(".", 1)
        value = (profile.get(section) or {}).get(key)
        if value is not None and normalise(path, value) not in options:
            bad.append(path)
    return bad


# Example values from the profile template. Left in place, they'd end up on a
# resume ("Your City, NSW") or typed into application forms. Defaults that
# can be someone's real answer (NSW, Australia, 2000) aren't counted.
_PLACEHOLDER_KEYS = {
    "personal": ("full_name", "preferred_name", "email", "password", "phone", "address", "city",
                 "linkedin_url", "github_url"),
    "resume_facts": ("preserved_companies", "preserved_projects", "preserved_school", "real_metrics"),
}
_PLACEHOLDER_SECTIONS = tuple(_PLACEHOLDER_KEYS)


def _template_examples() -> frozenset[str]:
    import json
    from pathlib import Path
    try:
        template = json.loads((Path(__file__).parent / "ui" / "profile_template.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    values = set()
    for section, keys in _PLACEHOLDER_KEYS.items():
        for key in keys:
            v = (template.get(section) or {}).get(key)
            values.update(str(x).strip().lower() for x in (v if isinstance(v, list) else [v]) if str(x or "").strip())
    return frozenset(values)


_EXAMPLES = _template_examples()


def is_placeholder(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in _EXAMPLES


def placeholder_fields(profile: dict) -> list[str]:
    """'section.key' for every field still holding a template example value."""
    found = []
    for section, keys in _PLACEHOLDER_KEYS.items():
        for key, value in (profile.get(section) or {}).items():
            if key not in keys:
                continue
            values = value if isinstance(value, list) else [value]
            if any(is_placeholder(v) for v in values):
                found.append(f"{section}.{key}")
    return found


def without_placeholders(profile: dict) -> dict:
    """The profile with template example values blanked, so they're never used as facts."""
    out = dict(profile)
    for section in _PLACEHOLDER_SECTIONS:
        fields = profile.get(section)
        if not isinstance(fields, dict):
            continue
        keys = _PLACEHOLDER_KEYS[section]
        out[section] = {k: v if k not in keys else [x for x in v if not is_placeholder(x)] if isinstance(v, list)
                        else ("" if is_placeholder(v) else v) for k, v in fields.items()}
    return out


def salary_problems(profile: dict) -> list[str]:
    """Salary figures that contradict each other (minimum above expected or maximum)."""
    comp = profile.get("compensation") or {}

    def num(key):
        digits = "".join(ch for ch in str(comp.get(key) or "") if ch.isdigit())
        return int(digits) if digits else None
    low, ask, high = num("salary_range_min"), num("salary_expectation"), num("salary_range_max")
    problems = []
    if low and ask and low > ask:
        problems.append("the salary range minimum is more than your expected salary")
    if low and high and low > high:
        problems.append("the salary range minimum is more than the maximum")
    if ask and high and ask > high:
        problems.append("your expected salary is more than the range maximum")
    return problems

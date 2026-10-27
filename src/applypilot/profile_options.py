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

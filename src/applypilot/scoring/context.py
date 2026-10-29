"""Candidate facts and house style shared by the AI prompts.

The prompts are tuned for engineers (software first, other disciplines
too) applying in Australia; the role, seniority, location and work rights
come from the profile.
"""

from __future__ import annotations

AU_STYLE = (
    "Use Australian English spelling (optimise, organisation, analyse, modelling, "
    "licence as a noun, programme only for non-software programs). Dates as "
    "\"Mar 2021 - Jun 2023\" in resumes and DD/MM/YYYY in forms. Currency is AUD."
)


def target_role(profile: dict) -> str:
    exp = profile.get("experience", {})
    return (exp.get("target_role") or exp.get("current_job_title") or "engineer").strip()


def current_title(profile: dict) -> str:
    """The job title the candidate actually holds (never the one they're applying for)."""
    exp = profile.get("experience", {})
    return str(exp.get("current_job_title") or exp.get("current_title") or "").strip()


def years(profile: dict) -> str:
    return str(profile.get("experience", {}).get("years_of_experience_total") or "").strip()


def _whole(value) -> int | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else None


def salary_floor(profile: dict) -> int | None:
    """The lowest base salary (excluding super) the candidate accepts: the range minimum,
    else the expected salary. Scoring and every form engine use this same number."""
    comp = profile.get("compensation", {})
    return _whole(comp.get("salary_range_min")) or _whole(comp.get("salary_expectation"))


def salary_ask(profile: dict) -> int | None:
    """What to ask for when the ad gives no pay: the expected salary, never below the floor."""
    floor, ask = salary_floor(profile), _whole(profile.get("compensation", {}).get("salary_expectation"))
    return max(x for x in (floor, ask) if x) if (floor or ask) else None


def _location(profile: dict) -> str:
    p = profile.get("personal", {})
    return ", ".join(x for x in (p.get("city"), p.get("province_state")) if x) or "Australia"


def candidate_brief(profile: dict) -> str:
    """The facts a recruiter screens on before reading the resume."""
    auth = profile.get("work_authorization", {})
    avail = profile.get("availability", {})
    comp = profile.get("compensation", {})
    yrs = years(profile)
    lines = [
        f"Target role: {target_role(profile)}" + (f", about {yrs} years of experience" if yrs else ""),
        f"Based in: {_location(profile)}. Willing to relocate: {avail.get('willing_to_relocate') or 'No'}",
        "Work rights: " + "; ".join(x for x in (
            f"authorised to work: {auth.get('legally_authorized_to_work') or 'unknown'}",
            f"needs sponsorship: {auth.get('require_sponsorship') or 'unknown'}",
            auth.get("work_permit_type") and f"status: {auth['work_permit_type']}",
        ) if x),
        f"Security clearance held: {auth.get('security_clearance') or 'none'}",
    ]
    wants = {kind: str(avail.get(key) or "").strip().lower()
             for kind, key in (("full-time", "available_for_full_time"), ("contract", "available_for_contract"))}
    yes = [k for k, v in wants.items() if v in ("yes", "true", "y")]
    no = [k for k, v in wants.items() if v in ("no", "false", "n")]
    if yes or no:
        lines.append("Work type: " + "; ".join(([f"wants {' or '.join(yes)}"] if yes else [])
                                               + ([f"not {' or '.join(no)}"] if no else [])))
    if salary_floor(profile):
        lines.append(f"Minimum salary: {salary_floor(profile)} {comp.get('salary_currency') or 'AUD'} "
                     "base, excluding super")
    return "\n".join(lines)


def work_rights(profile: dict) -> str:
    """Short work-rights line for a resume header ("Australian Citizen", "Full working rights")."""
    auth = profile.get("work_authorization", {})
    permit = str(auth.get("work_permit_type") or "").strip()
    if permit:
        low = permit.lower()
        if "citizen" in low:
            return "Australian Citizen" if "australia" in low or low == "citizen" else permit
        if "permanent" in low:
            return "Permanent Resident"
        return permit
    yes = lambda v: str(v).strip().lower() in ("yes", "true", "y")  # noqa: E731
    if yes(auth.get("legally_authorized_to_work")) and not yes(auth.get("require_sponsorship")):
        return "Full working rights"
    return ""


def location_line(profile: dict) -> str:
    """City and state for a resume header ("Sydney, NSW")."""
    p = profile.get("personal", {})
    return ", ".join(str(x).strip() for x in (p.get("city"), p.get("province_state")) if str(x or "").strip())

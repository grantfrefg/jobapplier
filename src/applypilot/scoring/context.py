"""Candidate facts and house style shared by the AI prompts.

The prompts are tuned for engineers (software first, other disciplines
too) applying in Australia or the UK (market.py); the role, seniority,
location and work rights come from the profile.
"""

from __future__ import annotations

from applypilot import market

AU_STYLE = market.AU.style  # the Australian house style (kept for older imports)


def house_style(profile: dict | None = None) -> str:
    """Spelling, dates and currency for the market in use."""
    return market.current(profile).style


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
    return ", ".join(x for x in (p.get("city"), p.get("province_state")) if x) or market.current(profile).country


def candidate_brief(profile: dict, local_areas: list[str] | None = None) -> str:
    """The facts a recruiter screens on before reading the resume.

    `local_areas` are the places the candidate searches in (searches.yaml): jobs there, or in any
    suburb of the same city, are local to them even when their home is a suburb the AI doesn't know.
    """
    auth = profile.get("work_authorization", {})
    avail = profile.get("availability", {})
    comp = profile.get("compensation", {})
    yrs = years(profile)
    lines = [
        f"Target role: {target_role(profile)}" + (f", about {yrs} years of experience" if yrs else ""),
        f"Based in: {_location(profile)}"
        + (f" (searching in: {'; '.join(local_areas)}; jobs there and in any of their suburbs are local)"
           if local_areas else "")
        + f". Willing to relocate: {avail.get('willing_to_relocate') or 'No'}",
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
    if "working holiday" in str(auth.get("work_permit_type") or "").lower():
        lines.append("Working holiday visa: usually up to 6 months with one employer. Contract, temporary, casual "
                     "and fixed-term roles of 6 months or less fit best. A permanent role scores 1 lower, including "
                     "one whose ad says nothing about visas (silence is not a dealbreaker: judge the rest as usual). "
                     "Only an ad that requires permanent residency or citizenship, or says no visa holders or no "
                     "sponsorship, is a work-rights dealbreaker.")
    if "youth mobility" in str(auth.get("work_permit_type") or "").lower():
        lines.append("Youth Mobility Scheme visa: full right to work in the UK for up to 2 years (3 for some), any "
                     "employer, no sponsorship needed. Contract, temporary and fixed-term roles fit best; a "
                     "permanent role is still fine. Only an ad that requires British citizenship, settled status or "
                     "long UK residency (e.g. for security clearance) is a work-rights dealbreaker; \"no "
                     "sponsorship\" is not.")
    if salary_floor(profile):
        mk = market.current(profile)
        lines.append(f"Minimum salary: {salary_floor(profile)} {comp.get('salary_currency') or mk.currency} "
                     f"{mk.pay_note}")
    return "\n".join(lines)


def work_rights(profile: dict) -> str:
    """Short work-rights line for a resume header ("Australian Citizen", "Full working rights")."""
    auth = profile.get("work_authorization", {})
    permit = str(auth.get("work_permit_type") or "").strip()
    if permit:
        low = permit.lower()
        if "british" in low and "irish" in low:
            return "British Citizen"
        if "citizen" in low:
            return "Australian Citizen" if "australia" in low or low == "citizen" else permit.title()
        if "permanent" in low:
            return "Permanent Resident"
        # UK: settled, pre-settled and Graduate visa holders can work without sponsorship
        if ("pre-settled" in low or "graduate" in low or "settled" in low or "indefinite leave" in low
                or "full right to work" in low):
            return "Right to work in the UK"
        if "youth mobility" in low:
            return "Youth Mobility Scheme visa"
        return permit
    yes = lambda v: str(v).strip().lower() in ("yes", "true", "y")  # noqa: E731
    if yes(auth.get("legally_authorized_to_work")) and not yes(auth.get("require_sponsorship")):
        return "Full working rights"
    return ""


def location_line(profile: dict) -> str:
    """City and state for a resume header ("Sydney, NSW")."""
    p = profile.get("personal", {})
    return ", ".join(str(x).strip() for x in (p.get("city"), p.get("province_state")) if str(x or "").strip())

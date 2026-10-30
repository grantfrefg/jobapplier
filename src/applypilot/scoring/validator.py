"""Resume and cover letter validation: banned words, fabrication detection, structural checks.

All validation is profile-driven -- no hardcoded personal data. The validator receives
a profile dict (from applypilot.config.load_profile()) and validates against the user's
actual skills, companies, projects, and school.

Validation modes
----------------
strict  -- banned words = hard errors that trigger retries (original behavior)
normal  -- banned words = warnings only; fabrication/structure = errors (default)
lenient -- banned words ignored; only fabrication and required structure checked
"""

import re
import logging

log = logging.getLogger(__name__)


# ── Universal Constants (not personal data) ───────────────────────────────

BANNED_WORDS: list[str] = [
    "passionate", "dedicated", "committed to",
    "utilizing", "utilize", "harnessing",
    "spearheaded", "spearhead", "orchestrated", "championed", "pioneered",
    "robust", "scalable solutions", "cutting-edge", "state-of-the-art", "best-in-class",
    "proven track record", "track record of success", "demonstrated ability",
    "strong communicator", "team player", "fast learner", "self-starter", "go-getter",
    "synergy", "cross-functional collaboration", "holistic",
    "transformative", "innovative solutions", "paradigm", "ecosystem",
    "proactive", "detail-oriented", "highly motivated",
    "seamless", "full lifecycle",
    "deep understanding", "extensive experience", "comprehensive knowledge",
    "thrives in", "excels at", "adept at", "well-versed in",
    "i am confident", "i believe", "i am excited",
    "plays a critical role", "instrumental in", "integral part of",
    "strong track record", "eager to", "eager",
    # Cover-letter-specific additions
    "this demonstrates", "this reflects", "i have experience with",
    "furthermore", "additionally", "moreover",
    # Words that mark text as AI-written to recruiters and AI detectors
    "leverage", "leveraged", "leveraging", "delve", "delved", "foster", "fostered", "fostering",
    "navigate", "navigating", "landscape", "tapestry", "realm", "pivotal", "meticulous", "meticulously",
    "dynamic", "fast-paced", "results-driven", "game-changer", "game-changing", "unlock", "unlocking",
    "empower", "empowering", "elevate", "elevating", "testament", "underscore", "underscores", "showcase",
    "showcasing", "honed", "resonate", "resonates", "thrilled", "excited to", "keen eye", "ever-evolving",
    "in today's", "impactful", "drive impact", "driving impact", "unwavering", "invaluable", "embark",
    "journey", "deep dive", "stakeholder buy-in", "value-add", "world-class", "next-level",
]

# Phrasings that read as AI-written, checked in the parts recruiters read most
# closely (the summary and the cover letter).
AI_PATTERNS: list[tuple[str, str]] = [
    (r"\bnot (?:just|only|merely)\b[^.]{0,80}\bbut\b", "'not just X but Y'"),
    (r"!", "exclamation marks"),
    (r"\?", "questions"),
    (r"\bi am writing to\b|\bi'm writing to\b", "'I am writing to'"),
    (r"\b(?:i am|i'm) (?:thrilled|delighted|excited|eager)\b", "gushing ('I'm thrilled')"),
    (r"\bwhether (?:it's|it is)\b", "'whether it's'"),
    (r"\bin (?:today's|the ever)", "'in today's ...'"),
    (r"\bnot only\b", "'not only'"),
    (r"\bi would be (?:a great|an excellent|the perfect) (?:fit|addition)\b", "'I would be a great fit'"),
]


def ai_phrasing(text: str) -> list[str]:
    """AI-sounding constructions in the text (see AI_PATTERNS)."""
    low = (text or "").lower()
    return sorted({name for pattern, name in AI_PATTERNS if re.search(pattern, low)})


def banned_in(text: str, allowed_text: str = "") -> list[str]:
    """Banned words in the text, except ones the candidate uses in their own resume."""
    low, own = (text or "").lower(), (allowed_text or "").lower()
    return [w for w in BANNED_WORDS
            if re.search(r"\b" + re.escape(w) + r"\b", low) and not re.search(r"\b" + re.escape(w) + r"\b", own)]


# American spellings an AI tends to use, with the Australian form. Only whole
# words, and never words the candidate's own resume spells that way (names).
_AU_SPELLING: dict[str, str] = {
    "optimize": "optimise", "optimized": "optimised", "optimizing": "optimising", "optimization": "optimisation",
    "optimizations": "optimisations", "organize": "organise", "organized": "organised", "organizing": "organising",
    "organization": "organisation", "organizations": "organisations", "organizational": "organisational",
    "analyze": "analyse", "analyzed": "analysed", "analyzing": "analysing", "modeling": "modelling",
    "modeled": "modelled", "behavior": "behaviour", "behaviors": "behaviours", "behavioral": "behavioural",
    "center": "centre", "centers": "centres", "color": "colour", "colors": "colours", "favor": "favour",
    "favorite": "favourite", "prioritize": "prioritise", "prioritized": "prioritised",
    "prioritizing": "prioritising",
    "standardize": "standardise", "standardized": "standardised", "summarize": "summarise", "summarized": "summarised",
    "utilization": "utilisation", "visualize": "visualise", "visualized": "visualised",
    "visualization": "visualisation",
    "visualizations": "visualisations", "personalize": "personalise", "personalized": "personalised",
    "personalization": "personalisation", "recognize": "recognise", "recognized": "recognised",
    "minimize": "minimise", "minimized": "minimised", "maximize": "maximise", "maximized": "maximised",
    "customize": "customise", "customized": "customised", "categorize": "categorise", "categorized": "categorised",
    "specialize": "specialise", "specialized": "specialised", "specializing": "specialising",
    "catalog": "catalogue", "catalogs": "catalogues", "labeled": "labelled", "labeling": "labelling",
    "traveled": "travelled", "canceled": "cancelled", "fulfill": "fulfil", "enrollment": "enrolment",
    "defense": "defence", "license": "licence", "program manager": "programme manager",
}


def australianise(text: str, original: str = "") -> str:
    """American to Australian spelling for AI-written text, keeping the candidate's own words."""
    if not text:
        return text
    own = (original or "").lower()

    def swap(m: re.Match) -> str:
        word = m.group(0)
        if re.search(r"\b" + re.escape(word.lower()) + r"\b", own):
            return word  # the candidate wrote it this way (a name, a product): keep it
        au = _AU_SPELLING[word.lower()]
        if word.isupper():
            return au.upper()
        return au[0].upper() + au[1:] if word[0].isupper() else au

    pattern = r"\b(" + "|".join(sorted(map(re.escape, _AU_SPELLING), key=len, reverse=True)) + r")\b"
    return re.sub(pattern, swap, text, flags=re.IGNORECASE)

LLM_LEAK_PHRASES: list[str] = [
    "i am sorry", "i apologize", "i will try", "let me try",
    "i am at a loss", "i am truly sorry", "apologies for",
    "i keep fabricating", "i will have to admit", "one final attempt",
    "one last time", "if it fails again", "persistent errors",
    "i am having difficulty", "i made an error", "my mistake",
    "here is the corrected", "here is the revised", "here is the updated",
    "here is my", "below is the", "as requested",
    "note:", "disclaimer:", "important:",
    "i have rewritten", "i have removed", "i have fixed",
    "i have replaced", "i have updated", "i have corrected",
    "per your feedback", "based on your feedback", "as per the instructions",
    "the following resume", "the resume below",
    "the following cover letter", "the letter below",
]

# Tools and claims the AI likes to add for a job that asks for them. Each is only
# fabricated when the candidate's resume and profile skills don't have it.
# Reasonable stretches (K8s, Terraform, Redis, Kafka etc.) are ALLOWED.
FABRICATION_WATCHLIST: set[str] = {
    # Languages with zero relation to the candidate's stack
    "c#", "c++", "golang", "rust", "ruby",
    "kotlin", "swift", "scala", "matlab",
    # Frameworks for wrong languages
    "spring", "django", "rails", "angular", "vue", "svelte",
    # Hard lies: certifications can't be stretched
    "certif", "certified", "pmp", "scrum master", "aws certified",
}

REQUIRED_SECTIONS: set[str] = {"SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}


# ── Helpers ───────────────────────────────────────────────────────────────

def _watch_pattern(term: str, word_start: bool = False) -> re.Pattern:
    """Whole-word match, so "rust" isn't found in "trust" or "scala" in "scalable". With
    word_start, a word that starts with the term counts too ("VueJS", "Swiftui")."""
    end = r"\w*" if term == "certif" or word_start else r"(?![\w+#])"
    return re.compile(r"(?<![\w+#])" + re.escape(term) + end)


def fabricated_terms(text: str, original_text: str = "", profile: dict | None = None) -> list[str]:
    """Watchlist terms in `text` that the resume and the profile's skills don't have."""
    from applypilot.scoring.facts import profile_skills
    known = ((original_text or "") + "\n" + " ".join(profile_skills(profile or {}))).lower()
    return sorted(t for t in FABRICATION_WATCHLIST
                  if _watch_pattern(t).search(text) and not _watch_pattern(t, word_start=True).search(known))


def _build_skills_set(profile: dict) -> set[str]:
    """Build the set of allowed skills from the profile's skills_boundary."""
    boundary = profile.get("skills_boundary", {})
    allowed: set[str] = set()
    for category in boundary.values():
        if isinstance(category, list):
            allowed.update(s.lower().strip() for s in category)
        elif isinstance(category, set):
            allowed.update(s.lower().strip() for s in category)
    return allowed


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues instead of rejecting."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")   # em dash -> comma
    text = text.replace("\u2013", "-")    # en dash -> hyphen
    text = text.replace("\u201c", '"').replace("\u201d", '"')   # smart double quotes
    text = text.replace("\u2018", "'").replace("\u2019", "'")   # smart single quotes
    return text.strip()


# ── JSON Field Validation ─────────────────────────────────────────────────

def validate_json_fields(data: dict, profile: dict, mode: str = "normal", original_text: str = "") -> dict:
    """Validate individual JSON fields from an LLM-generated tailored resume.

    Args:
        data:    Parsed JSON from the LLM (title, summary, skills, experience, projects, education).
        profile: User profile dict from load_profile().
        mode:    Validation strictness — "strict", "normal", or "lenient".
                 strict  → banned words are errors (trigger retries)
                 normal  → banned words are warnings (no retry)
                 lenient → banned words ignored entirely

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required keys — always checked regardless of mode
    # Projects are optional: requiring them pushes the AI to invent some
    for key in ("title", "summary", "skills", "experience", "education"):
        if key not in data or not data[key]:
            errors.append(f"Missing required field: {key}")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings}

    # Collect all text for bulk checks
    all_text_parts: list[str] = [data["summary"]]

    # Skills: check for fabrication (always enforced)
    if isinstance(data["skills"], dict):
        skills_text = " ".join(str(v) for v in data["skills"].values()).lower()
        for fake in fabricated_terms(skills_text, original_text, profile):
            errors.append(f"Fabricated skill: '{fake}'")

    # Experience: preserved companies must be present (always enforced)
    resume_facts = profile.get("resume_facts", {})
    preserved_companies = resume_facts.get("preserved_companies", [])

    if isinstance(data["experience"], list):
        for company in preserved_companies:
            has_company = any(
                company.lower() in str(e.get("header", "")).lower()
                for e in data["experience"]
            )
            if not has_company:
                errors.append(f"Company '{company}' missing from experience")
        for entry in data["experience"]:
            for b in entry.get("bullets", []):
                all_text_parts.append(b)

    # Projects: collect bullets
    if isinstance(data.get("projects"), list):
        for entry in data["projects"]:
            for b in entry.get("bullets", []):
                all_text_parts.append(b)

    # Education: preserved school must be present (always enforced)
    preserved_school = resume_facts.get("preserved_school", "")
    if preserved_school:
        edu = str(data.get("education", ""))
        if preserved_school.lower() not in edu.lower():
            errors.append(f"Education '{preserved_school}' missing")

    # Bulk text checks
    all_text = " ".join(all_text_parts).lower()

    # LLM self-talk is always an error regardless of mode (indicates broken output)
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in all_text]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # Banned filler words — severity depends on mode. The summary is read
    # most closely, so there they're errors in normal mode too.
    if mode != "lenient":
        found_banned = banned_in(all_text, original_text)
        in_summary = banned_in(str(data["summary"]), original_text)
        if found_banned:
            msg = f"Banned words: {', '.join(found_banned[:5])}"
            if mode == "strict" or in_summary:
                errors.append(f"Banned words in the summary: {', '.join(in_summary[:5])}" if in_summary
                              and mode != "strict" else msg)
            else:  # normal
                warnings.append(msg)
        patterns = ai_phrasing(str(data["summary"]))
        if patterns:
            errors.append(f"Summary reads as AI-written ({', '.join(patterns)}). Write it plainly.")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def new_numbers(original: str, generated: str) -> list[str]:
    """Numbers in `generated` that never appear in `original`.

    Invented metrics ("cut costs by 37%") are the most common fabrication,
    and they're cheap to spot in code. Single digits are ignored: counts
    like "2 teams" are too often legitimately reworded.
    """
    def numbers(text: str) -> set[str]:
        return {m.replace(",", "").rstrip(".") for m in _NUMBER_RE.findall(text or "")}

    known = numbers(original)
    return sorted(n for n in numbers(generated) - known if len(n.replace(".", "")) > 1)


# ── Full Resume Text Validation ───────────────────────────────────────────

def validate_tailored_resume(text: str, profile: dict, original_text: str = "") -> dict:
    """Programmatic validation of a tailored resume against the user's profile.

    Args:
        text: The tailored resume text to validate.
        profile: User profile dict from load_profile().
        original_text: The original base resume text (for fabrication comparison).

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    personal = profile.get("personal", {})
    resume_facts = profile.get("resume_facts", {})

    # 1. Check required sections exist (flexible matching)
    section_variants: dict[str, list[str]] = {
        "SUMMARY": ["summary", "professional summary", "profile"],
        "TECHNICAL SKILLS": ["technical skills", "skills", "tech stack", "core skills", "technologies"],
        "EXPERIENCE": ["experience", "work experience", "professional experience"],
        "EDUCATION": ["education", "academic background"],
    }
    for section, variants in section_variants.items():
        if not any(v in text_lower for v in variants):
            errors.append(f"Missing required section: {section} (or variant)")

    # 2. Check name preserved (warn, don't error -- we can inject it)
    full_name = personal.get("full_name", "")
    if full_name and full_name.lower() not in text_lower:
        warnings.append(f"Name '{full_name}' missing -- will be injected")

    # 3. Check companies preserved
    for company in resume_facts.get("preserved_companies", []):
        if company.lower() not in text_lower:
            errors.append(f"Company '{company}' missing -- cannot remove real experience")

    # 4. Check projects preserved
    for project in resume_facts.get("preserved_projects", []):
        if project.lower() not in text_lower:
            warnings.append(f"Project '{project}' not found -- may have been renamed")

    # 5. Check school preserved
    preserved_school = resume_facts.get("preserved_school", "")
    if preserved_school and preserved_school.lower() not in text_lower:
        errors.append(f"Education '{preserved_school}' missing")

    # 6. Check contact info preserved (warn, don't error -- we can inject)
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    if email and email.lower() not in text_lower:
        warnings.append("Email missing -- will be injected")
    if phone and phone not in text:
        warnings.append("Phone missing -- will be injected")

    # 7. Scan TECHNICAL SKILLS section for fabricated tools
    skills_start = text_lower.find("technical skills")
    skills_end = text_lower.find("experience", skills_start) if skills_start != -1 else -1
    if skills_start != -1 and skills_end != -1:
        skills_block = text_lower[skills_start:skills_end]
        for fake in fabricated_terms(skills_block, original_text, profile):
            errors.append(f"FABRICATED SKILL in Technical Skills: '{fake}'")

    # 8. Scan full document for fabrication watchlist items not in original
    if original_text:
        for fake in fabricated_terms(text_lower, original_text, profile):
            warnings.append(f"New tool/skill appeared: '{fake}' (not in original)")

    # 9. Em dashes (should be auto-fixed by sanitize_text, but safety net)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 10. Banned words (word-boundary matching)
    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found_banned:
        errors.append(f"Banned words: {', '.join(found_banned[:5])}")

    # 11. LLM self-talk leak detection
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 12. Duplicate section detection
    for section_name in ["summary", "experience", "education", "projects"]:
        count = text_lower.count(f"\n{section_name}\n") + text_lower.count(f"\n{section_name} \n")
        if text_lower.startswith(f"{section_name}\n"):
            count += 1
        if count > 1:
            errors.append(f"Section '{section_name}' appears {count} times.")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ── Cover Letter Validation ──────────────────────────────────────────────

def validate_cover_letter(text: str, mode: str = "normal", original_text: str = "") -> dict:
    """Programmatic validation of a cover letter.

    Args:
        text: The cover letter text to validate.
        mode: Validation strictness — "strict", "normal", or "lenient".
              strict  → banned words are errors (trigger retries); word limit enforced
              normal  → banned words are warnings; word limit is soft (+25 words)
              lenient → banned words ignored; word count not checked

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    # 1. Em dashes — always an error (sanitize_text should have caught these)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 2. Banned words and AI-sounding phrasing: a letter is read closely, so
    # these are errors unless lenient
    if mode != "lenient":
        found = banned_in(text, original_text)
        if found:
            errors.append(f"Banned words: {', '.join(found[:5])}")
        patterns = ai_phrasing(text)
        if patterns:
            errors.append(f"Reads as AI-written ({', '.join(patterns)}). Write it plainly.")

    # 3. Word count (an Australian cover letter fits on one page: 200-300 words)
    words = len(text.split())
    if mode == "strict" and words > 300:
        errors.append(f"Too long ({words} words). Max 300.")
    elif mode == "normal" and words > 330:
        warnings.append(f"Long ({words} words). Target 200-300.")
    # lenient: no word count check

    # 4. LLM self-talk — always an error regardless of mode
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 5. Must start with "Dear" — always checked (preamble should have been stripped)
    stripped = text.strip()
    if not stripped.lower().startswith("dear"):
        errors.append("Must start with 'Dear Hiring Manager,'")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}

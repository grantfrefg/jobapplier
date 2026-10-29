"""Sounding like the candidate, not like an AI.

- The tailoring style (TAILOR_STYLE): "full" rewrites for the ad; "light" keeps the
  candidate's own bullets and summary, only swapping in the ad's terms and reordering.
- Their writing sample (~/.applypilot/writing_sample.txt): cover letters follow its voice.
- Recent letters: a new letter mustn't open the way a recent one did.
- check(): the "reads human" report shown next to the ATS badge. No AI: it looks for the
  patterns recruiters notice in generated text.
"""

from __future__ import annotations

import os
import re
import statistics
from difflib import SequenceMatcher
from pathlib import Path

from applypilot import config

STYLES = ("full", "light")
MAX_SAMPLE_CHARS = 4000


def style() -> str:
    value = os.environ.get("TAILOR_STYLE", "").strip().lower()
    return value if value in STYLES else "full"


# -- writing sample ------------------------------------------------------------

def _sample_path() -> Path:
    return config.APP_DIR / "writing_sample.txt"


def writing_sample() -> str:
    try:
        return _sample_path().read_text(encoding="utf-8").strip()[:MAX_SAMPLE_CHARS]
    except OSError:
        return ""


def save_writing_sample(text: str) -> None:
    text = (text or "").strip()
    if text:
        _sample_path().parent.mkdir(parents=True, exist_ok=True)
        _sample_path().write_text(text[:MAX_SAMPLE_CHARS] + "\n", encoding="utf-8")
    else:
        _sample_path().unlink(missing_ok=True)


# -- letter openings -------------------------------------------------------------

_SALUTATION = re.compile(r"^\s*dear\b[^\n]*\n", re.IGNORECASE)


def opening(letter: str) -> str:
    """The letter's first sentence, after the "Dear ...," line."""
    body = _SALUTATION.sub("", (letter or "").strip(), count=1).strip()
    first = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0]
    return " ".join(first.split())[:300]


def recent_openings(limit: int = 6, skip: Path | None = None) -> list[str]:
    """First sentences of the most recent cover letters (newest first)."""
    folder = config.COVER_LETTER_DIR
    try:
        files = sorted(folder.glob("*_CL.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return []
    out = []
    for f in files:
        if skip is not None and f.resolve() == Path(skip).resolve():
            continue
        try:
            first = opening(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if first and first not in out:
            out.append(first)
        if len(out) >= limit:
            break
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def repeated_opening(letter: str, recent: list[str]) -> str | None:
    """The recent opening this letter's first sentence copies, if any."""
    first = _words(opening(letter))
    if len(first) < 4:
        return None
    for other in recent:
        words = _words(other)
        if first[:6] == words[:6] or SequenceMatcher(None, first, words).ratio() >= 0.75:
            return other
    return None


def letter_voice_rules() -> str:
    """Extra cover-letter instructions: the candidate's own voice, and openings to avoid."""
    parts = []
    sample = writing_sample()
    if sample:
        parts.append(
            "THE CANDIDATE'S OWN VOICE: below is something they wrote themselves. Write the letter the way "
            "they write: match its tone, formality, sentence length and how it opens and closes. Never copy "
            "its sentences, and take every fact from the resume, not from this sample.\n"
            f"<<<\n{sample}\n>>>")
    recent = recent_openings()
    if recent:
        parts.append("RECENT LETTERS OPENED LIKE THIS. Open this one differently (different first words "
                     "and a different angle):\n" + "\n".join(f"- {r}" for r in recent))
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


# -- light touch ---------------------------------------------------------------

LIGHT_TOUCH_RULES = """## STYLE: LIGHT TOUCH (overrides the rewriting rules above)
The candidate wants their resume to stay in their own words.
- BULLETS: copy each original bullet word for word. Only change a bullet to swap in the ad's term for
  the same thing ("web services" -> "REST APIs") or for Australian spelling. Reorder bullets within a
  role so the job's must-haves come first, and drop the least relevant ones if a role has more than 5.
- SUMMARY: start from the original summary and change at most one sentence, so it leads with what this
  job needs. If the resume has no summary, write 2 short, plain sentences.
- SKILLS: reorder, and use the ad's name for a skill they have; otherwise leave the wording alone."""


def _bullet_lines(text: str) -> list[str]:
    return [line.strip().lstrip("-•*·").strip() for line in (text or "").splitlines()
            if re.match(r"^\s*[-•*·]\s+\S", line)]


def _own_words(bullet: str, originals: list[list[str]]) -> bool:
    words = _words(bullet)
    return bool(words) and any(SequenceMatcher(None, words, o).ratio() >= 0.85 for o in originals)


def kept_share(bullets: list[str], original_resume: str) -> float | None:
    """Share of the bullets that are (nearly) word for word from the original resume."""
    if not bullets:
        return None
    originals = [_words(line) for line in (original_resume or "").splitlines() if len(_words(line)) >= 3]
    return sum(_own_words(b, originals) for b in bullets) / len(bullets)


def light_touch_problems(resume: dict, original_resume: str) -> list[str]:
    """In light-touch mode, most bullets must be the candidate's own words."""
    bullets = [str(b) for section in ("experience", "projects") for entry in resume.get(section) or []
               if isinstance(entry, dict) for b in entry.get("bullets") or []]
    share = kept_share(bullets, original_resume)
    if share is not None and share < 0.6:
        return [f"Light touch: only {round(share * 100)}% of bullets are the original wording. Copy the "
                "original bullets word for word, changing only a term the ad names differently."]
    return []


# -- reads human ---------------------------------------------------------------

_STOCK = ["proven track record", "results-driven", "team player", "go-getter", "hit the ground running",
          "fast-paced environment", "wealth of experience", "strong work ethic", "think outside the box",
          "excellent communication skills", "make a difference", "add value", "perfect fit", "dream job",
          "i am confident", "i believe i", "unique blend", "passion for"]
_TRIPLE = re.compile(r"\b[\w-]+(?: [\w-]+){0,2}, [\w-]+(?: [\w-]+){0,2},? and [\w-]+")


def _summary(resume_text: str) -> str:
    match = re.search(r"^SUMMARY\s*\n(.+?)(?:\n\s*\n|\Z)", resume_text or "", re.S | re.M)
    return match.group(1) if match else ""


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if len(s.split()) >= 3]


def check(resume_text: str, letter: str = "", job_ad: str = "", original_resume: str = "",
          recent: list[str] | None = None) -> dict:
    """What might make these documents read as AI-written: {"issues": [...], "own_words": share or None}."""
    from applypilot.scoring.validator import ai_phrasing, banned_in
    issues: list[str] = []
    bullets = _bullet_lines(resume_text)
    prose = _summary(resume_text) + "\n" + (letter or "")
    everything = (resume_text or "") + "\n" + (letter or "")

    words = banned_in(everything, original_resume)
    stock = [s for s in _STOCK if s in everything.lower() and s not in (original_resume or "").lower()]
    if words or stock:
        issues.append("Stock phrases recruiters spot: " + ", ".join(dict.fromkeys(words + stock)))
    phrasing = ai_phrasing(prose)
    if phrasing:
        issues.append("AI-sounding phrasing: " + ", ".join(phrasing))
    if "—" in everything or "–" in everything:
        issues.append("Long dashes (— or –), a common sign of AI text: use commas or full stops")

    if len(bullets) >= 4:
        firsts = [(_words(b) or [""])[0] for b in bullets]
        common, count = max(((w, firsts.count(w)) for w in set(firsts)), key=lambda x: x[1])
        if count >= 3 and count / len(firsts) >= 0.3:
            issues.append(f'{count} of {len(bullets)} bullets start with "{common.capitalize()}": vary the verbs')
    if len(bullets) >= 6:
        lengths = [len(_words(b)) for b in bullets]
        if statistics.mean(lengths) and statistics.pstdev(lengths) / statistics.mean(lengths) < 0.15:
            issues.append("Every bullet is about the same length: real resumes mix short and long ones")

    triples = _TRIPLE.findall(prose)
    if len(triples) >= 3:
        issues.append(f'{len(triples)} lists of three ("X, Y and Z") in the summary and letter: cut some')
    letter_sentences = _sentences(letter)
    if len(letter_sentences) >= 5:
        with_i = sum(1 for s in letter_sentences if re.match(r"(?i)i\b", s))
        if with_i / len(letter_sentences) > 0.5:
            issues.append(f'{with_i} of {len(letter_sentences)} letter sentences start with "I"')

    ad = _words(job_ad)
    if len(ad) >= 6:
        ad_runs = {" ".join(ad[i:i + 6]) for i in range(len(ad) - 5)}
        mine = _words(everything)
        copied = {" ".join(mine[i:i + 6]) for i in range(len(mine) - 5)} & ad_runs
        if len(copied) >= 4:
            issues.append(f"{len(copied)} phrases of 6+ words copied from the ad: say them your way")

    if letter and recent:
        same = repeated_opening(letter, recent)
        if same:
            issues.append(f'The letter opens like another recent one ("{same[:70]}…")')

    share = kept_share(bullets, original_resume) if original_resume else None
    return {"issues": issues, "own_words": None if share is None else round(share * 100)}

"""Your stories: short true accounts in your own words (Setup -> Your stories).

A resume says what you did; a story says how it went: the problem, what you
did, what happened, a detail only you would know. Generated letters and form
answers sound generic because the AI only has the resume. With these:

- Cover letters: the one or two stories closest to the job's ad are given to
  the AI to build the letter around (retold briefly, nothing added).
- The fact-check: a letter's details and numbers that come from a story count
  as real.
- Application forms: "Tell us about a time you..." questions are answered from
  them, by the built-in engine and by Claude Code / Codex.
Resume bullets stay tied to the resume itself.

Saved in ~/.applypilot/stories.json.
"""

from __future__ import annotations

import json
import re

from applypilot import config

QUESTIONS = {
    "proud": "What are you most proud of in this job?",
    "problem": "A problem you solved: what was wrong, what did you do, how did it turn out?",
    "pressure": "A time things went wrong or got tight (deadline, mistake, conflict). How did you handle it?",
    "people": "Working with others: a colleague, customer or stakeholder moment that went well.",
    "learned": "Something you had to learn quickly, and how you used it.",
    "why": "What do you enjoy about this kind of work, or what draws you to the jobs you're applying for?",
}
MAX_STORIES = 40
MAX_ANSWER = 1500

_STOP = set("a an and are as at be but by for from has have i in is it its me my of on or our so that the "
            "their them then there they this to was we were what when which while who will with you your "
            "about after all also any can did do had how into just more not one out over some than very".split())


def path():
    return config.APP_DIR / "stories.json"


def load() -> list[dict]:
    """[{"role": "Acme (Analyst)", "question": "proud", "answer": "..."}]"""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s in data if isinstance(s, dict) and str(s.get("answer") or "").strip()]


def save(stories: list[dict]) -> list[dict]:
    clean = []
    for s in stories[:MAX_STORIES]:
        answer = " ".join(str(s.get("answer") or "").split())[:MAX_ANSWER]
        if not answer:
            continue
        question = s.get("question") if s.get("question") in QUESTIONS else "proud"
        clean.append({"role": " ".join(str(s.get("role") or "").split())[:80], "question": question,
                      "answer": answer})
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return clean


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]*[a-z0-9+#]|[a-z]", (text or "").lower())
            if w not in _STOP and len(w) > 2}


def for_job(job: dict | None, limit: int = 2) -> list[dict]:
    """The stories closest to this job's ad (shared words), best first; the "why" answer
    always comes along when there is one, for "why this role" questions."""
    stories = load()
    if not stories:
        return []
    ad = _words(" ".join(str((job or {}).get(k) or "") for k in ("title", "full_description", "score_reasoning")))
    ranked = sorted(stories, key=lambda s: len(_words(s["answer"] + " " + s["role"]) & ad), reverse=True)
    picked = [s for s in ranked if s["question"] != "why"][:limit]
    why = next((s for s in stories if s["question"] == "why"), None)
    return picked + ([why] if why else [])


def _line(s: dict) -> str:
    where = f"[{s['role']}] " if s.get("role") else ""
    return f"- {where}{QUESTIONS[s['question']]}\n  In their words: {s['answer']}"


def letter_block(job: dict | None) -> str:
    """The stories for a cover letter prompt, or ""."""
    chosen = for_job(job)
    if not chosen:
        return ""
    return ("\n\nTHE CANDIDATE'S OWN STORIES (true, in their words; for the cover letter only, never the resume). "
            "Build the letter around the one that fits this job best: retell it briefly in the first person, "
            "keep its specific details, and add nothing it doesn't say. If none fits, don't force one in.\n"
            + "\n".join(_line(s) for s in chosen))


def answers_block() -> str:
    """All stories, for answering application questions ("Tell us about a time..."), or ""."""
    stories = load()
    if not stories:
        return ""
    return ("THE APPLICANT'S OWN STORIES (true, in their words). For questions like \"tell us about a time "
            "you...\", \"describe a challenge\" or \"why this role\", answer from the story that fits, in the "
            "first person, keeping its details and adding nothing it doesn't say:\n"
            + "\n".join(_line(s) for s in stories))


def facts_text() -> str:
    """The stories as plain text, added to the resume when a letter's facts are checked."""
    return "\n".join(f"{s.get('role', '')}: {s['answer']}" for s in load())

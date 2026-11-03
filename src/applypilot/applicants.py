"""How many people have applied to a job, when the board says (LinkedIn does).

Read from the job page the app already loads: JobSpy's LinkedIn detail page during
discovery, or the detail page during enrichment. Stored as display text
("47 applicants", "200+ applicants", "Under 25 applicants") and a number for sorting.
"""

from __future__ import annotations

import re

_CAPTION = re.compile(r'num-applicants__caption[^>]*>\s*([^<]{3,80}?)\s*<', re.I)
_FIRST = re.compile(r"\bbe among the first (\d[\d,]*) applicants\b", re.I)
_OVER = re.compile(r"\b(?:over|more than) (\d[\d,]*) applicants\b", re.I)
_COUNT = re.compile(r"\b(\d[\d,]*)(\+)? applicants\b", re.I)


def parse(text: str) -> tuple[str, int] | None:
    """("47 applicants", 47) from a job page's HTML or text, or None when it doesn't say."""
    if not text:
        return None
    caption = _CAPTION.search(text)
    sample = caption.group(1) if caption else text
    for pattern, shown, count in (
        (_FIRST, "Under {n} applicants", lambda n: max(n // 2, 1)),
        (_OVER, "{n}+ applicants", lambda n: n),
        (_COUNT, "{n} applicants", lambda n: n),
    ):
        match = pattern.search(sample)
        if match:
            n = int(match.group(1).replace(",", ""))
            if pattern is _COUNT and match.group(2):
                return f"{n}+ applicants", n
            return shown.format(n=n), count(n)
    return None


# -- JobSpy's LinkedIn scraper: read the count from the page it already fetches ---------

_FOUND: dict[str, tuple[str, int]] = {}  # LinkedIn job id -> (text, number)


def watch_linkedin() -> None:
    """Have JobSpy's LinkedIn detail fetch note each page's applicant count (no extra requests)."""
    try:
        from jobspy.linkedin import LinkedIn
    except Exception:  # JobSpy not installed, or laid out differently
        return
    original = getattr(LinkedIn, "_fetch_details", None)
    if original is None or getattr(original, "_counts_applicants", False):
        return

    def fetch_details(self, job_id):
        get = self.session.get
        page = {}

        def remember(url, *args, **kwargs):
            response = get(url, *args, **kwargs)
            page["html"] = getattr(response, "text", "") or ""
            return response
        self.session.get = remember
        try:
            details = original(self, job_id)
        finally:
            self.session.get = get
        found = parse(page.get("html", ""))
        if found:
            _FOUND[str(job_id)] = found
        return details

    fetch_details._counts_applicants = True
    LinkedIn._fetch_details = fetch_details


def for_url(url: str) -> tuple[str, int] | None:
    """The count noted for a LinkedIn job URL during this run, if any."""
    match = re.search(r"/jobs/view/(\d+)", url or "")
    return _FOUND.get(match.group(1)) if match else None

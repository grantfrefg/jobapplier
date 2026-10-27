"""Where a job's application really happens, and whether it needs an account.

Job boards (Jora, Indeed, LinkedIn) only show the ad; the form is on the
employer's application system. `locate_jobs` opens each ready job in the
local engine's visible browser (the one with your logins, which also gets
past Jora's bot check), follows the ad's Apply link and records where it
lands, so the Apply tab can show e.g. "Greenhouse · no account" and you can
do the quick ones first. It only navigates: it never fills or clicks
anything but plain Apply links, and costs no AI requests.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# (pattern on the application URL, key, display name, account needed)
#   "no"    - fill and submit without an account
#   "board" - your login on that job board (stays logged in in the app's browser)
#   "yes"   - an account with this employer (once per employer)
_SYSTEMS: list[tuple[str, str, str, str]] = [
    (r"greenhouse\.io|gh_jid=", "greenhouse", "Greenhouse", "no"),
    (r"lever\.co", "lever", "Lever", "no"),
    (r"smartrecruiters\.com", "smartrecruiters", "SmartRecruiters", "no"),
    (r"ashbyhq\.com", "ashby", "Ashby", "no"),
    (r"workable\.com", "workable", "Workable", "no"),
    (r"bamboohr\.com", "bamboohr", "BambooHR", "no"),
    (r"jobadder\.com", "jobadder", "JobAdder", "no"),
    (r"teamtailor\.com", "teamtailor", "Teamtailor", "no"),
    (r"breezy\.hr", "breezy", "Breezy", "no"),
    (r"myworkdayjobs\.com|myworkdaysite\.com|workday\.com", "workday", "Workday", "yes"),
    (r"icims\.com", "icims", "iCIMS", "yes"),
    (r"taleo\.net", "taleo", "Taleo", "yes"),
    (r"successfactors\.|sapsf\.", "successfactors", "SuccessFactors", "yes"),
    (r"oraclecloud\.com", "oracle", "Oracle", "yes"),
    (r"pageuppeople\.com", "pageup", "PageUp", "yes"),
    (r"seek\.com\.au", "seek", "SEEK", "board"),
    (r"linkedin\.com", "linkedin", "LinkedIn", "board"),
    (r"indeed\.com", "indeed", "Indeed", "board"),
    (r"jora\.com", "jora", "Jora", "board"),
]

ACCOUNT_LABELS = {"no": "no account", "board": "job board login", "yes": "account needed", "": "not checked"}


def classify(url: str | None, password_field: bool = False, easy_apply: bool = False) -> dict:
    """{"site", "name", "account"} for an application page URL."""
    if easy_apply:
        return {"site": "linkedin_easy", "name": "LinkedIn Easy Apply", "account": "board"}
    u = (url or "").lower()
    for pattern, key, name, account in _SYSTEMS:
        if re.search(pattern, u):
            break
    else:
        host = urlsplit(u).netloc.removeprefix("www.")
        key, name, account = "employer", host or "Employer site", "unknown"
    if password_field and account in ("no", "unknown"):
        account = "yes"  # the page itself asks for a sign-in
    elif account == "unknown":
        account = "no"
    return {"site": key, "name": name, "account": account}


_PAGE_STATE_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const buttons = [...document.querySelectorAll('button, a, [role=button]')].filter(vis)
    .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase());
  return {
    password: [...document.querySelectorAll('input[type=password]')].some(vis),
    easy_apply: buttons.some(t => t.startsWith('easy apply')),
    challenge: /just a moment|verify you are human|checking your browser/i.test(document.title + ' ' +
               (document.body ? document.body.innerText.slice(0, 300) : '')),
  };
}"""


def _page_state(page) -> dict:
    state = {"password": False, "easy_apply": False, "challenge": False}
    for frame in page.frames:
        try:
            s = frame.evaluate(_PAGE_STATE_JS)
        except Exception:
            continue
        for k in state:
            state[k] = state[k] or bool(s.get(k))
    return state


def _settle(page, seconds: float = 12) -> dict:
    """Wait out a bot-check page (Cloudflare on Jora) and return the page state."""
    deadline = time.time() + seconds
    state = _page_state(page)
    while state["challenge"] and time.time() < deadline:
        page.wait_for_timeout(1000)
        state = _page_state(page)
    return state


def locate(page, start_url: str) -> dict:
    """Open a job and follow its ad to the application page. Navigation only."""
    from applypilot.apply import local_engine as le

    page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
    state = _settle(page)
    for _ in range(3):
        if not le._is_job_listing(page.url) or state["challenge"]:
            break
        if state["easy_apply"]:
            return {**classify(page.url, easy_apply=True), "url": page.url}
        _, controls, _ = le._scan(page)
        href = le._find_apply_link(controls)
        if not href:
            break
        page.goto(href, wait_until="domcontentloaded", timeout=45000)
        state = _settle(page)
    if state["challenge"]:
        raise RuntimeError("blocked by the site's bot check")
    newest = le._newest_tab(page)
    if newest is not page:  # some Apply links open a new tab
        page = newest
        state = _settle(page)
    if le._is_job_listing(page.url):
        # The Apply button isn't a link (e.g. LinkedIn sends you through a button)
        board = classify(page.url)
        return {"site": board["site"], "name": board["name"] + " (Apply button)", "account": "unknown",
                "url": page.url}
    return {**classify(page.url, password_field=state["password"]), "url": page.url}


def jobs_to_locate(conn, min_score: int = 7, recheck: bool = False) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE (tailored_resume_path IS NOT NULL OR own_resume_path IS NOT NULL) "
        "AND applied_at IS NULL "
        "AND duplicate_of IS NULL AND COALESCE(apply_status, '') != 'in_progress' "
        "AND COALESCE(fit_score, 0) >= ? " + ("" if recheck else "AND apply_site IS NULL ") +
        "ORDER BY fit_score DESC", (min_score,)).fetchall()
    return [dict(r) for r in rows]


def locate_jobs(min_score: int = 7, recheck: bool = False, limit: int = 0) -> dict:
    """Check where each ready job applies and save it. Returns counts."""
    from playwright.sync_api import sync_playwright

    from applypilot.apply import local_engine as le
    from applypilot.database import apply_link, get_connection, mark_duplicates

    conn = get_connection()
    mark_duplicates(conn)
    jobs = jobs_to_locate(conn, min_score, recheck)
    if limit:
        jobs = jobs[:limit]
    if not jobs:
        logger.info("Every ready job has already been checked.")
        return {"checked": 0, "failed": 0}
    logger.info("Checking where %d job(s) apply (a browser window opens; nothing is filled in)...", len(jobs))
    checked = failed = 0
    with sync_playwright() as p:
        context = le.launch_browser(p)
        try:
            for job in jobs:
                page = context.new_page()
                try:
                    found = locate(page, apply_link(job))
                    conn.execute("UPDATE jobs SET apply_site = ?, apply_site_name = ?, apply_account = ?, "
                                 "apply_site_url = ?, apply_site_checked_at = ? WHERE url = ?",
                                 (found["site"], found["name"], found["account"], found["url"],
                                  datetime.now(timezone.utc).isoformat(), job["url"]))
                    conn.commit()
                    checked += 1
                    logger.info("  %-45s %s · %s", job["title"][:45], found["name"],
                                ACCOUNT_LABELS.get(found["account"], found["account"]))
                except Exception as e:
                    failed += 1
                    logger.warning("  %-45s couldn't check: %s", job["title"][:45], str(e).splitlines()[0][:120])
                finally:
                    for tab in context.pages[1:]:
                        try:
                            tab.close()
                        except Exception:
                            pass
        finally:
            context.close()
    logger.info("Done: %d checked, %d couldn't be checked.", checked, failed)
    return {"checked": checked, "failed": failed}

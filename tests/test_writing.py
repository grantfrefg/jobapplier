"""Tailored resume + cover letter in one request, checked against the real resume."""

import copy
import json

import pytest

from applypilot import config, database
from applypilot.scoring import cover_letter as cl_mod
from applypilot.scoring import tailor

PROFILE = {
    "personal": {"full_name": "Jane Doe", "preferred_name": "Jane", "email": "jane@example.com",
                 "phone": "0412 345 678"},
    "skills_boundary": {"languages": ["SQL", "Python"], "tools": ["Power BI"]},
    "resume_facts": {"preserved_companies": ["Acme Health"], "preserved_projects": ["Ward Dashboard"],
                     "preserved_school": "UNSW", "real_metrics": ["10 hours to 2"]},
    "experience": {"education_level": "BSc"},
}
RESUME = (
    "Jane Doe\nData Analyst at Acme Health, 2019 - 2023\n"
    "- Cut weekly reporting from 10 hours to 2 with SQL and Python\n"
    "- Built the Ward Dashboard in Power BI\nUNSW, BSc\n"
)
JOB = {"url": "https://ex.com/1", "title": "Data Analyst", "company": "Sydney Health", "site": "indeed",
       "location": "Sydney", "full_description": "We need SQL and Power BI to report on ward capacity."}

GOOD_RESUME = {
    "title": "Data Analyst",
    "summary": "Data analyst who cut weekly reporting from 10 hours to 2 at Acme Health.",
    "skills": {"Languages": "SQL, Python", "Tools": "Power BI"},
    "experience": [{"header": "Data Analyst at Acme Health", "subtitle": "SQL | 2019 - 2023",
                    "bullets": ["Cut weekly reporting from 10 hours to 2 with SQL and Python"]}],
    "projects": [{"header": "Ward Dashboard - Power BI", "subtitle": "Power BI",
                  "bullets": ["Built a ward capacity dashboard in Power BI"]}],
    "education": "UNSW | BSc",
}
GOOD_LETTER = ("Dear Hiring Manager,\n\nAt Acme Health I cut weekly reporting from 10 hours to 2 with SQL.\n\n"
               "I built the Ward Dashboard in Power BI, the same kind of capacity reporting you need.\n\n"
               "Happy to walk through any of this in more detail.\n\nJane")


class FakeLLM:
    """Plays back queued replies and records which kind of request each was."""

    def __init__(self, combined=(), judge=(), letters=()):
        self.combined, self.judge, self.letters = list(combined), list(judge), list(letters)
        self.calls: list[str] = []

    def chat_json(self, messages, temperature=0.0, max_tokens=4096):
        if "resume fact-checker" in messages[0]["content"]:
            self.calls.append("judge")
            return self.judge.pop(0) if self.judge else {"verdict": "PASS", "issues": "none"}
        self.calls.append("combined")
        reply = self.combined.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        self.calls.append("letter")
        return self.letters.pop(0)


def combined(resume=None, letter=GOOD_LETTER):
    return {"resume": copy.deepcopy(resume or GOOD_RESUME), "cover_letter": letter}


@pytest.fixture
def use(monkeypatch):
    def install(fake):
        monkeypatch.setattr(tailor, "get_client", lambda: fake)
        monkeypatch.setattr(cl_mod, "get_client", lambda: fake)
        return fake
    return install


def test_clean_output_is_written_then_fact_checked(use):
    fake = use(FakeLLM(combined=[combined()]))
    resume, letter, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge"]  # the AI fact-check always reads it (except lenient)
    assert report["status"] == "approved" and report["requests"] == 2
    assert resume.startswith("Jane Doe\nData Analyst\njane@example.com")  # header injected by code
    assert letter.startswith("Dear Hiring Manager,") and letter.endswith("Jane")


def test_invented_metric_is_sent_back(use):
    padded = copy.deepcopy(GOOD_RESUME)
    padded["experience"][0]["bullets"].append("Grew dashboard adoption by 37%")
    fake = use(FakeLLM(combined=[combined(padded), combined()]))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "combined", "judge"]  # code caught it; no judge needed for that
    assert report["status"] == "approved"


def test_judge_failure_regenerates_both(use):
    fake = use(FakeLLM(combined=[combined(), combined()],
                       judge=[{"verdict": "FAIL", "issues": "joined two facts"}, {"verdict": "PASS"}]))
    _, letter, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge", "combined", "judge"]
    assert report["status"] == "approved" and report["requests"] == 4
    assert letter == GOOD_LETTER


def test_judge_rejection_after_last_attempt_is_not_used(use):
    use(FakeLLM(combined=[combined()] * 4, judge=[{"verdict": "FAIL", "issues": "invented work"}] * 4))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert report["status"] == "failed_judge"


def test_invented_numbers_in_cover_letter_are_caught(use):
    letter = GOOD_LETTER.replace("10 hours to 2", "40 hours to 2")
    fake = use(FakeLLM(combined=[combined(letter=letter)], letters=[GOOD_LETTER]))
    _, final, _ = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge", "letter"] and final == GOOD_LETTER


def test_bad_letter_alone_gets_letter_only_retry(use):
    fake = use(FakeLLM(combined=[combined(letter="Here is my cover letter: hi")], letters=[GOOD_LETTER]))
    resume, letter, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge", "letter"]
    assert report["status"] == "approved" and report["requests"] == 3
    assert letter == GOOD_LETTER


def test_missing_letter_gets_letter_only_retry(use):
    fake = use(FakeLLM(combined=[{"resume": copy.deepcopy(GOOD_RESUME)}], letters=[GOOD_LETTER]))
    _, letter, _ = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge", "letter"] and letter == GOOD_LETTER


def test_resume_validation_failure_retries_with_notes(use):
    no_company = copy.deepcopy(GOOD_RESUME)
    no_company["experience"][0]["header"] = "Data Analyst at Somewhere"
    fake = use(FakeLLM(combined=[combined(no_company), combined()]))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "combined", "judge"] and report["status"] == "approved"


def test_unreadable_json_retries(use):
    fake = use(FakeLLM(combined=[ValueError("not json"), combined()]))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "combined", "judge"] and report["requests"] == 3


@pytest.mark.parametrize("mode, calls", [("strict", ["combined", "judge"]), ("lenient", ["combined"])])
def test_validation_modes(use, mode, calls):
    fake = use(FakeLLM(combined=[combined()]))
    tailor.tailor_application(RESUME, JOB, PROFILE, validation_mode=mode)
    assert fake.calls == calls


def test_prompt_names_the_hiring_company():
    assert "COMPANY: Sydney Health" in cl_mod.job_text(JOB)


def test_run_tailoring_saves_resume_and_cover_letter(tmp_path, monkeypatch, use):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "load_search_config", lambda: {})
    for mod in (tailor, cl_mod):
        monkeypatch.setattr(mod, "load_profile", lambda: PROFILE)
        monkeypatch.setattr(mod, "RESUME_PATH", tmp_path / "resume.txt")
    (tmp_path / "resume.txt").write_text(RESUME)
    monkeypatch.setattr(tailor, "TAILORED_DIR", tmp_path / "tailored")
    monkeypatch.setattr(tailor, "COVER_LETTER_DIR", tmp_path / "covers")
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", lambda path: path)  # no browser needed
    conn = database.init_db(db)
    conn.execute("INSERT INTO jobs (url, title, company, site, location, full_description, fit_score) "
                 "VALUES (?, ?, ?, ?, ?, ?, 9)", (JOB["url"], JOB["title"], JOB["company"], JOB["site"],
                                                  JOB["location"], JOB["full_description"]))
    conn.commit()
    fake = use(FakeLLM(combined=[combined()]))

    result = tailor.run_tailoring(min_score=7)
    assert result["approved"] == 1 and result["cover_letters"] == 1 and result["requests"] == 2
    row = conn.execute("SELECT tailored_resume_path, cover_letter_path FROM jobs").fetchone()
    assert row[0].endswith("Sydney_Health_Data_Analyst.txt")
    assert open(row[1]).read() == GOOD_LETTER
    # The separate cover-letter stage now has nothing to do for this job
    assert cl_mod.run_cover_letters(min_score=7)["generated"] == 0
    assert fake.calls == ["combined", "judge"]
    database.close_connection(db)
    report = json.loads((tmp_path / "tailored" / "Sydney_Health_Data_Analyst_REPORT.json").read_text())
    assert report["requests"] == 2


@pytest.fixture
def tailoring_db(tmp_path, monkeypatch, use):
    db = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "load_search_config", lambda: {})
    for mod in (tailor, cl_mod):
        monkeypatch.setattr(mod, "load_profile", lambda: PROFILE)
        monkeypatch.setattr(mod, "RESUME_PATH", tmp_path / "resume.txt")
    (tmp_path / "resume.txt").write_text(RESUME)
    monkeypatch.setattr(tailor, "TAILORED_DIR", tmp_path / "tailored")
    monkeypatch.setattr(tailor, "COVER_LETTER_DIR", tmp_path / "covers")
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", lambda path: path)
    conn = database.init_db(db)

    def add(url, title, company, score, **extra):
        cols = {"url": url, "title": title, "company": company, "site": "indeed", "location": "Sydney",
                "full_description": JOB["full_description"], "fit_score": score, **extra}
        conn.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))
        conn.commit()

    yield conn, add
    database.close_connection(db)


def test_redo_rewrites_exactly_the_marked_job_whatever_its_score(tailoring_db, use):
    conn, add = tailoring_db
    add("https://ex.com/old", "Data Analyst", "Sydney Health", 6, tailored_resume_path="/old/resume.txt",
        cover_letter_path="/old/cl.txt")
    add("https://ex.com/new", "BI Analyst", "Other Co", 9)  # waiting for a normal tailor run
    assert database.redo_documents(conn, url="https://ex.com/old") == 1
    fake = use(FakeLLM(combined=[combined()]))
    result = tailor.run_tailoring(min_score=8, limit=0, redo_only=True)
    assert result["approved"] == 1 and fake.calls == ["combined", "judge"]  # the other job wasn't touched
    row = conn.execute("SELECT tailored_resume_path, cover_letter_path, redo_requested FROM jobs "
                       "WHERE url = 'https://ex.com/old'").fetchone()
    assert row[0].endswith("Sydney_Health_Data_Analyst.txt") and row[1].endswith("_CL.txt") and row[2] == 0
    assert conn.execute("SELECT tailored_resume_path FROM jobs WHERE url = 'https://ex.com/new'").fetchone()[0] is None


def test_failed_redo_keeps_the_old_documents(tailoring_db, use):
    conn, add = tailoring_db
    add("https://ex.com/old", "Data Analyst", "Sydney Health", 9, tailored_resume_path="/old/resume.txt",
        cover_letter_path="/old/cl.txt")
    database.redo_documents(conn)
    bad = copy.deepcopy(GOOD_RESUME)
    bad["experience"][0]["header"] = "Data Analyst at Somewhere"  # drops a preserved company every time
    use(FakeLLM(combined=[combined(bad)] * 4))
    tailor.run_tailoring(redo_only=True, limit=0)
    row = conn.execute("SELECT tailored_resume_path, cover_letter_path, redo_requested FROM jobs").fetchone()
    assert tuple(row) == ("/old/resume.txt", "/old/cl.txt", 0)
    assert not (tailor.TAILORED_DIR / "Sydney_Health_Data_Analyst.txt").exists()  # draft kept aside


def test_redo_interrupted_by_budget_stays_marked(tailoring_db, use):
    from applypilot.budget import BudgetExhausted
    conn, add = tailoring_db
    add("https://ex.com/old", "Data Analyst", "Sydney Health", 9, tailored_resume_path="/old/resume.txt")
    database.redo_documents(conn)
    use(FakeLLM(combined=[BudgetExhausted("out of requests")]))
    tailor.run_tailoring(redo_only=True, limit=0)
    row = conn.execute("SELECT tailored_resume_path, redo_requested FROM jobs").fetchone()
    assert tuple(row) == ("/old/resume.txt", 1)  # picked up again on the next run


def test_rejected_job_records_why_and_the_log_explains_it(tailoring_db, use, caplog):
    conn, add = tailoring_db
    add("https://ex.com/stretch", "Senior Data Scientist", "Big Co", 9)
    use(FakeLLM(combined=[combined()] * 4, judge=[{"verdict": "FAIL", "issues": "claims a senior title"}] * 4))
    with caplog.at_level("INFO", logger=tailor.log.name):
        result = tailor.run_tailoring(min_score=7)
    assert result["failed"] == 1
    row = conn.execute("SELECT tailored_resume_path, tailor_status, tailor_issues, tailor_attempts "
                       "FROM jobs").fetchone()
    assert tuple(row) == (None, "failed_judge", "claims a senior title", 1)
    assert ("The fact-check rejected every draft: claims a senior title. No resume or cover letter is used for "
            "this job; it's tried again on the next run (4 of 5 tries left).") in caplog.text
    assert "1 job(s) got no documents this run" in caplog.text

    # A later success clears the reason
    use(FakeLLM(combined=[combined()]))
    tailor.run_tailoring(min_score=7)
    row = conn.execute("SELECT tailor_status, tailor_issues FROM jobs").fetchone()
    assert tuple(row) == ("approved", None)


def test_explain_failure_wording():
    assert tailor.explain_failure("failed_validation", "", 5).endswith(
        "it won't be tried again unless you press Try again on the Apply tab (All jobs).")
    assert tailor.explain_failure("failed_judge", "x", 1, kept_old=True) == (
        "The fact-check rejected every draft: x. The job keeps its previous resume and cover letter.")

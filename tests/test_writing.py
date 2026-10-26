"""Tailored resume + cover letter in one request, judge only when needed."""

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
        if "resume quality judge" in messages[0]["content"]:
            self.calls.append("judge")
            return self.judge.pop(0)
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


def test_clean_output_costs_one_request(use):
    fake = use(FakeLLM(combined=[combined()]))
    resume, letter, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined"]
    assert report["status"] == "approved" and report["requests"] == 1
    assert report["judge"]["verdict"] == "SKIPPED"
    assert resume.startswith("Jane Doe\nData Analyst\njane@example.com")  # header injected by code
    assert letter.startswith("Dear Hiring Manager,") and letter.endswith("Jane")


def test_invented_metric_triggers_judge(use):
    padded = copy.deepcopy(GOOD_RESUME)
    padded["experience"][0]["bullets"].append("Grew dashboard adoption by 37%")
    fake = use(FakeLLM(combined=[combined(padded)], judge=[{"verdict": "PASS", "issues": "none"}]))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge"]
    assert report["status"] == "approved" and report["requests"] == 2
    assert "numbers not in your resume: 37" in report["judge_reasons"][0]


def test_judge_failure_regenerates_both(use):
    padded = copy.deepcopy(GOOD_RESUME)
    padded["summary"] = "Built services in Golang."
    fake = use(FakeLLM(combined=[combined(padded), combined()],
                       judge=[{"verdict": "FAIL", "issues": "invented Golang"}]))
    _, letter, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge", "combined"]
    assert report["status"] == "approved" and report["requests"] == 3
    assert letter == GOOD_LETTER


def test_invented_numbers_in_cover_letter_are_caught(use):
    letter = GOOD_LETTER.replace("10 hours to 2", "40 hours to 2")
    fake = use(FakeLLM(combined=[combined(letter=letter)], judge=[{"verdict": "PASS", "issues": "none"}]))
    tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "judge"]


def test_bad_letter_alone_gets_letter_only_retry(use):
    fake = use(FakeLLM(combined=[combined(letter="Here is my cover letter: hi")], letters=[GOOD_LETTER]))
    resume, letter, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "letter"]
    assert report["status"] == "approved" and report["requests"] == 2
    assert letter == GOOD_LETTER


def test_missing_letter_gets_letter_only_retry(use):
    fake = use(FakeLLM(combined=[{"resume": copy.deepcopy(GOOD_RESUME)}], letters=[GOOD_LETTER]))
    _, letter, _ = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "letter"] and letter == GOOD_LETTER


def test_resume_validation_failure_retries_with_notes(use):
    no_company = copy.deepcopy(GOOD_RESUME)
    no_company["experience"][0]["header"] = "Data Analyst at Somewhere"
    fake = use(FakeLLM(combined=[combined(no_company), combined()]))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "combined"] and report["status"] == "approved"


def test_unreadable_json_retries(use):
    fake = use(FakeLLM(combined=[ValueError("not json"), combined()]))
    _, _, report = tailor.tailor_application(RESUME, JOB, PROFILE)
    assert fake.calls == ["combined", "combined"] and report["requests"] == 2


@pytest.mark.parametrize("mode, calls", [("strict", ["combined", "judge"]), ("lenient", ["combined"])])
def test_validation_modes(use, mode, calls):
    padded = copy.deepcopy(GOOD_RESUME)
    padded["summary"] += " Grew adoption 37%."
    fake = use(FakeLLM(combined=[combined(padded if mode == "lenient" else None)],
                       judge=[{"verdict": "PASS", "issues": "none"}]))
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
    assert result["approved"] == 1 and result["cover_letters"] == 1 and result["requests"] == 1
    row = conn.execute("SELECT tailored_resume_path, cover_letter_path FROM jobs").fetchone()
    assert row[0].endswith("Sydney_Health_Data_Analyst.txt")
    assert open(row[1]).read() == GOOD_LETTER
    # The separate cover-letter stage now has nothing to do for this job
    assert cl_mod.run_cover_letters(min_score=7)["generated"] == 0
    assert fake.calls == ["combined"]
    database.close_connection(db)
    report = json.loads((tmp_path / "tailored" / "Sydney_Health_Data_Analyst_REPORT.json").read_text())
    assert report["requests"] == 1

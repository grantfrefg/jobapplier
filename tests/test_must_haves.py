"""The scorer's must-haves the resume lacks: stored per job, used by the ATS check and the fact-check
in any field, with no per-field word lists."""

import re

from applypilot import database
from applypilot.scoring import ats, facts, scorer, tailor

BA_RESUME = """MORGAN LEE
Business Analyst, Harbourview Bank | 2022 - Present
- Wrote user stories in Jira; mapped processes in BPMN (Visio); ran UAT.
"""
BA_JOB = {"title": "Business Analyst - Salesforce", "full_description": "Salesforce BA. Salesforce Administrator "
          "certification essential. Requirements, user stories, process mapping.",
          "score_reasoning": "user stories, process mapping\nStrong BA, no Salesforce.",
          "missing_must_haves": "Salesforce, Salesforce Administrator certification, Lean Six Sigma"}


class FakeAI:
    def __init__(self, missing):
        self.missing = missing

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        ids = re.findall(r"### POSTING id=(\w+)", messages[-1]["content"])
        return {"results": [{"id": i, "score": 6, "keywords": ["user stories", "BPMN"], "missing": self.missing,
                             "reasoning": "Missing Salesforce."} for i in ids]}


def test_the_scorer_returns_what_the_resume_lacks(monkeypatch):
    for reply in (["Salesforce", "Salesforce Administrator certification"],
                  "Salesforce, Salesforce Administrator certification"):
        monkeypatch.setattr(scorer, "get_client", lambda r=reply: FakeAI(r))
        got = scorer.score_batch(BA_RESUME, [{"url": "u1", "title": "BA", "full_description": "x"}])["u1"]
        assert got["missing"] == "Salesforce, Salesforce Administrator certification"
        assert got["keywords"] == "user stories, BPMN"
    prompt = scorer.score_prompt()
    assert '"missing":' in prompt and "short term in the posting's own words" in prompt


def test_scoring_stores_the_missing_must_haves(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    resume = tmp_path / "resume.txt"
    resume.write_text(BA_RESUME)
    monkeypatch.setattr(scorer, "RESUME_PATH", resume)
    monkeypatch.setattr(scorer, "load_search_config", lambda: {})
    monkeypatch.setattr(scorer, "load_profile", lambda: {})
    monkeypatch.setattr(scorer, "get_client", lambda: FakeAI("Salesforce"))
    conn = database.init_db(path)
    conn.execute("INSERT INTO jobs (url, title, site, full_description) VALUES ('u1', 'BA', 'x', 'ad')")
    conn.commit()
    scorer.run_scoring()
    assert conn.execute("SELECT missing_must_haves FROM jobs").fetchone()[0] == "Salesforce"
    database.close_connection(path)


def test_claiming_a_missing_must_have_is_a_fabrication_in_any_field():
    source = facts.Source(BA_RESUME)
    missing = facts.missing_terms(BA_JOB)
    assert facts.claimed_missing("Led Salesforce implementations and requirements.", missing, source) == ["Salesforce"]
    assert facts.claimed_missing("Wrote user stories and ran UAT.", missing, source) == []
    # The scorer can be wrong: a must-have the resume does mention (in any known form) is never flagged
    assert facts.claimed_missing("Search engine optimisation", ["SEO"], facts.Source("Ran SEO audits.")) == []
    assert facts.claimed_missing("Ran SEO audits", ["search engine optimisation"],
                                 facts.Source("Did search engine optimisation.")) == []


def test_tailoring_rejects_a_resume_or_letter_that_claims_one():
    resume = {"title": "Business Analyst", "summary": "Business analyst who led Salesforce rollouts.",
              "skills": {"Analysis": "user stories, BPMN"}, "experience": [], "education": ""}
    letter = "Dear Hiring Manager,\n\nI hold the Salesforce Administrator certification.\n\nKind regards,\nMorgan"
    out = tailor.check_facts(resume, letter, BA_RESUME, {}, BA_JOB)
    assert any("salesforce" in e.lower() and "never does" in e for e in out["errors"])
    assert out["letter_errors"] == ["Mentions Salesforce Administrator certification, which the resume never does."]
    clean = {**resume, "summary": "Business analyst who wrote user stories and ran UAT."}
    assert not any("never does" in e for e in tailor.check_facts(clean, "", BA_RESUME, {}, BA_JOB)["errors"])


def test_ats_keywords_include_what_the_scorer_found_missing():
    terms = {t.lower() for t in ats.ad_terms(BA_JOB)}
    assert {"user stories", "process mapping", "salesforce administrator certification", "lean six sigma"} <= terms
    report = ats.check(BA_RESUME + "\nSUMMARY\nBA\nSKILLS\nuser stories, process mapping\nEXPERIENCE\nx\n", BA_JOB)
    assert "Lean Six Sigma" in report["missing_gap"] and "user stories" in report["matched"]


def test_a_named_dealbreaker_caps_the_score(monkeypatch):
    """The AI sometimes wrote "Dealbreaker: ..." and still scored 4 (scoring eval): the rule is kept in code."""
    replies = {"Location dealbreaker: onsite in Melbourne.": 3, "DEALBREAKER (seniority far off).": 3,
               "Dealbreaker: contract-only and the candidate wants full-time work.": 4,
               "Working holiday: silence on visas is not a dealbreaker.": 7,
               "Remote, so location isn't a dealbreaker.": 7,
               "No dealbreakers; strong fit.": 7, "Great match.": 7}

    class AI:
        def chat_json(self, messages, temperature=0.0, max_tokens=0):
            return {"results": [{"id": f"j{i}", "score": 7, "keywords": "", "missing": "", "reasoning": r}
                                for i, r in enumerate(replies, 1)]}
    monkeypatch.setattr(scorer, "get_client", lambda: AI())
    jobs = [{"url": f"u{i}", "title": "x", "full_description": "x"} for i in range(1, len(replies) + 1)]
    got = scorer.score_batch("resume", jobs)
    assert [got[f"u{i}"]["score"] for i in range(1, len(replies) + 1)] == list(replies.values())


def test_years_of_experience_are_not_must_have_terms():
    job = {"missing_must_haves": "12+ years in business analysis, 3 yrs SQL, Salesforce, PMP or PRINCE2 certification"}
    assert facts.missing_terms(job) == ["Salesforce", "PMP or PRINCE2 certification"]

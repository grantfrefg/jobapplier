"""The scorer's must-haves the resume lacks: stored per job, used by the ATS check and the fact-check
in any field, with no per-field word lists."""

import re

import pytest

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


# -- invented titles from the ad itself (no word lists) ------------------------------

PO_JOB = {"title": "Product Owner (Payments)", "full_description": "Own the backlog."}
BA_TEXT = "MORGAN LEE\nSenior Business Analyst, Harbourview Bank\n- Wrote user stories."
BA_SOURCE = facts.Source(BA_TEXT)


def test_the_ads_core_title():
    for raw, core in [("Senior Business Analyst - Salesforce", "business analyst"),
                      ("Marketing Coordinator (6 month contract)", "marketing coordinator"),
                      ("Lead Data Engineer | Remote", "data engineer"),
                      ("Graduate Software Engineer 2027", "software engineer"),
                      ("Paralegal", "paralegal"), ("", "")]:
        assert facts.ad_title({"title": raw}) == core


@pytest.mark.parametrize("summary, flagged", [
    ("Experienced product owner with 6 years in banking.", True),
    ("A product owner with 6 years in banking.", True),
    ("Business analyst who acted as product owner for two releases.", True),
    ("Business analyst ready for the Product Owner role.", False),
    ("Kept the backlog groomed with the product owner.", False),
    ("Worked with a product owner on two teams.", False),
    ("Partnered with product owners and testers.", False),
    ("Business analyst moving into a product owner role.", False),
])
def test_the_ads_title_given_to_the_candidate_is_caught(summary, flagged):
    assert bool(facts.ad_title_claims(summary, PO_JOB, BA_SOURCE)) is flagged


def test_in_a_letter_only_first_person_claims_count():
    claims = facts.ad_title_claims
    assert claims("As a product owner, I set priorities.", PO_JOB, BA_SOURCE, first_person=True) == ["product owner"]
    assert claims("In my 6 years as a product owner I shipped...", PO_JOB, BA_SOURCE, first_person=True)
    assert not claims("I'm keen on the Product Owner role.", PO_JOB, BA_SOURCE, first_person=True)
    assert not claims("I worked daily with the product owner.", PO_JOB, BA_SOURCE, first_person=True)


def test_any_field_and_titles_the_candidate_holds():
    legal = facts.Source("SAM ROE\nLegal Assistant, Hart & Co\n- Prepared court documents.")
    assert facts.ad_title_claims("Experienced paralegal with 4 years.", {"title": "Paralegal"}, legal) == ["paralegal"]
    coord = facts.Source("JO\nMarketing Coordinator, Bramble\n- Ran Instagram.")
    assert facts.ad_title_claims("Marketing manager who grew a brand.", {"title": "Marketing Manager"}, coord)
    assert not facts.ad_title_claims("Marketing coordinator who ran Instagram.", {"title": "Marketing Coordinator"},
                                     coord)  # their own title: fine
    assert not facts.ad_title_claims("Experienced business analyst.", {"title": "Senior Business Analyst - SAP"},
                                     BA_SOURCE)


def test_tailoring_sends_back_the_ads_title_as_the_candidates():
    resume = {"title": "Senior Business Analyst", "summary": "Experienced product owner with 6 years in banking.",
              "skills": {}, "experience": [], "education": ""}
    letter = "Dear Hiring Manager,\n\nAs a product owner I set priorities.\n\nKind regards,\nMorgan"
    out = tailor.check_facts(resume, letter, BA_TEXT, {}, PO_JOB)
    assert any("product owner" in e for e in out["errors"])
    assert any("product owner" in e for e in out["letter_errors"])


# -- banned words: the job ad's own phrases are fine (no per-field lists) ---------------

NURSE_AD = ("Registered Nurse. You'll improve the patient journey across the ward and work in a dynamic team "
            "environment. We're passionate about care.")


@pytest.mark.parametrize("text, ad, banned", [
    ("Improved the patient journey for 40 beds.", NURSE_AD, []),
    ("Improved the patient journey for 40 beds.", "", ["journey"]),  # without the ad: as before
    ("A dynamic nurse on a journey.", NURSE_AD, ["dynamic", "journey"]),  # filler, though the ad says "dynamic"
    ("Passionate about care.", NURSE_AD, ["passionate"]),
    ("Improved the patient journey; a dynamic nurse.", NURSE_AD, ["dynamic"]),
    ("Monitored the cloud ecosystem daily.", "Monitor the threat landscape and our cloud ecosystem.", []),
    ("Mapped the vendor ecosystem.", "Monitor the threat landscape and our cloud ecosystem.", ["ecosystem"]),
])
def test_a_phrase_the_ad_uses_is_not_a_banned_word(text, ad, banned):
    from applypilot.scoring.validator import banned_in
    assert banned_in(text, "", ad) == banned


def test_the_ad_reaches_the_letter_and_resume_checks():
    from applypilot.scoring import cover_letter
    from applypilot.scoring.validator import validate_cover_letter, validate_json_fields
    letter = "Dear Hiring Manager,\n\nOn my ward I improved the patient journey for 40 beds.\n\nKind regards,\nSam"
    assert any("Banned" in e for e in validate_cover_letter(letter)["errors"])
    assert not any("Banned" in e for e in validate_cover_letter(letter, job_ad=NURSE_AD)["errors"])
    resume = {"title": "Registered Nurse", "summary": "Nurse who improved the patient journey for 40 beds.",
              "skills": {"Clinical": "wound care"}, "education": "Uni | BN",
              "experience": [{"header": "Registered Nurse at Ward 4", "bullets": ["Ran wound care."]}]}
    without = validate_json_fields(resume, {}, original_text="Registered Nurse")
    with_ad = validate_json_fields(resume, {}, original_text="Registered Nurse", job_ad=NURSE_AD)
    assert any("journey" in e for e in without["errors"]) and not any("journey" in e for e in with_ad["errors"])
    assert "phrase the job ad itself uses" in tailor._tailor_rules({})
    assert "phrase the job ad itself uses" in cover_letter.cover_letter_rules({})

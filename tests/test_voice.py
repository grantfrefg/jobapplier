"""Sounding like the candidate: tailoring style, their writing sample, varied letters, the reads-human check."""

from applypilot import config
from applypilot.scoring import cover_letter, tailor, voice

ORIGINAL = """Jane Doe
SUMMARY
Backend engineer who builds data services in Python.
EXPERIENCE
Software Engineer, Acme
- Built a Python ingestion service on AWS Lambda handling 2M events a day
- Cut reporting lag from 6 hours to 15 minutes by moving batch jobs to streaming
- Wrote the on-call runbook and trained four engineers to use it
"""
PROFILE = {"personal": {"full_name": "Jane Doe"}, "experience": {"current_job_title": "Software Engineer"}}


def test_style_setting(monkeypatch):
    assert voice.style() == "full"
    monkeypatch.setenv("TAILOR_STYLE", "light")
    assert voice.style() == "light"
    assert "LIGHT TOUCH" in tailor._build_application_prompt(PROFILE)
    monkeypatch.setenv("TAILOR_STYLE", "nonsense")
    assert voice.style() == "full" and "LIGHT TOUCH" not in tailor._build_application_prompt(PROFILE)


def test_light_touch_sends_back_a_rewrite():
    kept = {"experience": [{"bullets": [
        "Built a Python ingestion service on AWS Lambda handling 2M events a day",
        "Cut reporting lag from 6 hours to 15 minutes by moving batch jobs to streaming",
        "Wrote the on-call runbook and trained four engineers to use it"]}]}
    assert voice.light_touch_problems(kept, ORIGINAL) == []
    rewritten = {"experience": [{"bullets": [
        "Engineered event pipelines processing millions of records daily",
        "Delivered near-real-time reporting for the business",
        "Wrote the on-call runbook and trained four engineers to use it"]}]}
    assert "only 33% of bullets" in voice.light_touch_problems(rewritten, ORIGINAL)[0]


def test_writing_sample_and_recent_openings_shape_the_letter():
    assert voice.writing_sample() == "" and "OWN VOICE" not in cover_letter.cover_letter_rules(PROFILE)
    voice.save_writing_sample("Hi Sam,\nShort and plain is how I write. Cheers, Jane")
    folder = config.COVER_LETTER_DIR
    folder.mkdir(parents=True)
    letter = ("Dear Hiring Manager,\n\nThe payments API you're rebuilding is the kind of system I have run "
              "for three years. More here.\n")
    (folder / "A_Backend_CL.txt").write_text(letter, encoding="utf-8")
    rules = cover_letter.cover_letter_rules(PROFILE)
    assert "OWN VOICE" in rules and "Short and plain is how I write." in rules
    assert "Open this one differently" in rules and "The payments API you're rebuilding" in rules
    # A letter opening the same way is caught (and retried); a different one isn't
    recent = voice.recent_openings()
    same = "Dear Sam,\nThe payments API you're rebuilding is the kind of system I have run for years. Thanks."
    assert voice.repeated_opening(same, recent)
    different = "Dear Sam,\nAt Acme I built the ingestion service for 2M events a day."
    assert voice.repeated_opening(different, recent) is None
    voice.save_writing_sample("   ")
    assert voice.writing_sample() == ""


def test_reads_human_flags_ai_patterns():
    ai = """Jane Doe
SUMMARY
Results-driven engineer with a proven track record — not just building systems but shaping them.

EXPERIENCE
- Built data pipelines for analytics teams across the company
- Built monitoring dashboards for operations teams across the company
- Built deployment tooling for platform teams across the business
- Built testing frameworks for product teams across the business
"""
    ad = ("You will build data pipelines for analytics teams across the company and monitoring dashboards for "
          "operations teams across the company")
    letter = ("Dear Hiring Manager,\n\nI build things for teams. I test things. I ship things. "
              "I like Python, SQL and AWS. I know APIs, queues and caches. I enjoy design, testing and delivery.")
    report = voice.check(ai, letter, ad, ORIGINAL, ["I build things for teams like yours."])
    text = " | ".join(report["issues"])
    assert "Stock phrases" in text and "proven track record" in text and "results-driven" in text
    assert "'not just X but Y'" in text and "Long dashes" in text
    assert '4 of 4 bullets start with "Built"' in text
    assert "lists of three" in text and 'start with "I"' in text
    assert "copied from the ad" in text and "opens like another recent one" in text
    assert report["own_words"] == 0

    plain = voice.check(ORIGINAL, "Dear Sam,\n\nAt Acme I built the ingestion service. It handles 2M events a day.",
                        "Python engineer", ORIGINAL, [])
    assert plain == {"issues": [], "own_words": 100}

"""Your stories: true accounts in your own words, used by cover letters and application answers."""

from applypilot import stories
from applypilot.apply import local_engine as le
from applypilot.scoring import tailor

from test_facts import GOOD, PROFILE, RESUME

FREIGHT = {"role": "Harbourline Retail Group", "question": "problem",
           "answer": "Returns at the Parramatta depot were piling up, so I mapped every step with the floor team "
                     "and we cut the backlog by 18% in six weeks."}
WHY = {"role": "", "question": "why", "answer": "I like making messy operations calm and predictable."}
MODELS = {"role": "Harbourline Retail Group", "question": "proud",
          "answer": "Shipping the two-tower recommender to all customers after a year of experiments."}


def test_saving_and_choosing_the_closest_stories():
    assert stories.load() == [] and stories.letter_block({"title": "x"}) == ""
    saved = stories.save([FREIGHT, MODELS, WHY, {"role": "x", "question": "proud", "answer": "   "},
                          {"role": "y", "question": "made-up", "answer": "Kept"}])
    assert len(saved) == 4 and saved[-1]["question"] == "proud"  # empty dropped, unknown question fixed
    ops = {"title": "Operations Analyst", "full_description": "Improve depot returns and backlog with the floor team"}
    chosen = stories.for_job(ops)
    assert chosen[0] == FREIGHT and chosen[-1] == WHY  # closest first; "why" always comes along
    block = stories.letter_block(ops)
    assert "cut the backlog by 18%" in block and "never the resume" in block
    assert "Tell us about a time" not in block and "tell us about a time" in stories.answers_block()


def test_a_storys_facts_count_as_real_in_the_letter():
    letter = ("Dear Hiring Manager,\n\nAt Harbourline I mapped every step of depot returns with the floor team "
              "and we cut the backlog by 18% in six weeks.\n\nKind regards,\nAlex")

    def numbers():
        return [e for e in tailor.check_facts(dict(GOOD), letter, RESUME, PROFILE)["letter_errors"] if "18" in e]
    assert numbers()  # without the story, 18% is invented
    stories.save([FREIGHT])
    assert numbers() == []


def test_application_forms_get_the_stories_for_free_text_questions():
    stories.save([FREIGHT])
    block = stories.answers_block()
    question = [{"id": "q1", "type": "textarea", "label": "Tell us about a time you solved a problem"}]
    assert "backlog by 18%" in le._build_user_prompt({"title": "Analyst"}, question, {}, "", False, False, None,
                                                    stories=block)
    choices = [{"id": "q2", "type": "select", "label": "Do you have a licence?"}]
    assert "backlog" not in le._build_user_prompt({"title": "Analyst"}, choices, {}, "", False, False, None,
                                                  stories=block)

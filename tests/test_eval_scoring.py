"""The scoring eval (evals/scoring): its dataset, metrics and runner, with a fake AI (no real calls)."""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the evals package lives at the repo root

from applypilot import config, llm  # noqa: E402
from applypilot.scoring import scorer  # noqa: E402
from evals.scoring import __main__ as cli  # noqa: E402
from evals.scoring import dataset, metrics, run  # noqa: E402


def rec(job, label, scores, dealbreaker=None, candidate="c"):
    return {"candidate": candidate, "job": job, "label": label, "dealbreaker": dealbreaker, "scores": scores}


# -- the dataset ---------------------------------------------------------------

def test_dataset_is_valid_and_covers_the_prompts_rules():
    candidates = dataset.load()
    assert len(candidates) >= 3 and sum(len(c.jobs) for c in candidates) >= 25
    kinds = {j.dealbreaker for c in candidates for j in c.jobs if j.dealbreaker}
    assert kinds >= {"work_rights", "clearance", "location", "seniority", "work_type"}
    for c in candidates:
        scores = [j.score for j in c.jobs]
        assert max(scores) >= 8 and min(scores) <= 3, f"{c.id} needs strong and weak matches"
        assert c.profile["personal"]["city"] and c.local_areas
        assert len({j.company for j in c.jobs}) == len(c.jobs)  # the fake AI below tells jobs apart by company


@pytest.mark.parametrize("label, error", [
    ({"score": 11, "why": "x"}, "1 to 10"),
    ({"score": "7", "why": "x"}, "1 to 10"),
    ({"score": 5, "dealbreaker": "location", "why": "x"}, "3 or lower"),
    ({"score": 4, "dealbreaker": "work_type", "why": "x"}, None),  # work type allows 4
    ({"score": 2, "dealbreaker": "vibes", "why": "x"}, "dealbreaker must be"),
    ({"score": 7}, "why"),
])
def test_bad_labels_are_caught(tmp_path, label, error):
    case = {"id": "x", "resume": "R", "profile": {"personal": {}},
            "jobs": [{"id": "j", "title": "T", "description": "D", "label": label}]}
    (tmp_path / "x.yaml").write_text(json.dumps(case))  # JSON is valid YAML
    if error is None:
        assert dataset.load(tmp_path)[0].jobs[0].score == 4
    else:
        with pytest.raises(dataset.DatasetError, match=error):
            dataset.load(tmp_path)


def test_unknown_candidate_is_named():
    with pytest.raises(dataset.DatasetError, match="no such candidate: nobody"):
        dataset.load(only=["nobody"])


# -- metrics ---------------------------------------------------------------------

def test_spearman():
    assert metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1)
    assert metrics.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1)
    assert metrics.spearman([1, 2, 2, 3], [1, 3, 3, 4]) == pytest.approx(1)  # ties share a rank
    assert metrics.spearman([5, 5, 5], [1, 2, 3]) is None  # no variation: undefined
    assert metrics.spearman([1, 2], [1, 2]) is None


def test_summary_numbers():
    records = [
        rec("a", 9, [8, 9, 9]),                         # median 9: exact
        rec("b", 6, [8, 8, 7]),                         # median 8: +2
        rec("c", 3, [3, 3, 3], dealbreaker="location"),  # caught
        rec("d", 4, [6, 7, 6], dealbreaker="work_type"),  # median 6 > 4: let through
        rec("e", 7, [2, 3, None]),                      # median 2.5: a good job sunk; one score missing
    ]
    s = metrics.summarise(records)
    assert s["jobs"] == 5 and s["scored"] == 5 and s["missing"] == 1
    assert s["mae"] == round((0 + 2 + 0 + 2 + 4.5) / 5, 2) and s["bias"] == round((0 + 2 + 0 + 2 - 4.5) / 5, 2)
    assert s["within_1"] == 0.4 and s["exact"] == 0.4
    assert s["dealbreaker_recall"] == 0.5 and s["dealbreakers_missed"] == ["c/d"]
    assert s["false_dealbreakers"] == ["c/e"]
    assert s["unstable"] == 0 and s["mean_spread"] == round((1 + 1 + 0 + 1 + 1) / 5, 2)
    assert [m["case"] for m in metrics.biggest_misses(records)] == ["c/e", "c/b", "c/d"]


def test_nothing_scored_gives_empty_numbers_not_errors():
    s = metrics.summarise([rec("a", 5, [None, None])])
    assert s["mae"] is None and s["spearman"] is None and s["missing"] == 2 and s["dealbreaker_recall"] is None


def test_compare_says_better_or_worse():
    old = {"mae": 1.5, "within_1": 0.6, "bias": 0.8, "missing": 2, "spearman": 0.7}
    new = {"mae": 1.0, "within_1": 0.5, "bias": -0.3, "missing": 2, "spearman": 0.8}
    lines = "\n".join(metrics.compare(old, new))
    assert re.search(r"mae .*\(better\)", lines) and re.search(r"within_1 .*\(worse\)", lines)
    assert re.search(r"bias .*\(better\)", lines) and re.search(r"spearman .*\(better\)", lines)
    assert re.search(r"missing +2 -> +2$", lines, re.M)  # unchanged: no verdict


# -- the runner, with a fake AI ---------------------------------------------------

class FakeAI:
    """Answers every posting with its human label plus `offset`, except companies in `drop`."""

    model = "fake-model"

    def __init__(self, offset=0, drop=()):
        labels = {j.company: j.score for c in dataset.load() for j in c.jobs}
        self.labels, self.offset, self.drop = labels, offset, set(drop)
        self.calls: list[list[dict]] = []

    def chat_json(self, messages, temperature=0.0, max_tokens=4096):
        self.calls.append(messages)
        results = []
        for job_id, company in re.findall(r"### POSTING id=(\w+)\nTITLE: .*\nCOMPANY: (.*)", messages[-1]["content"]):
            if company not in self.drop:
                score = max(1, min(10, self.labels[company] + self.offset))
                results.append({"id": job_id, "score": score, "keywords": "", "reasoning": f"{company} reason"})
        return {"results": results}


@pytest.fixture
def fake(monkeypatch):
    def use(ai):
        monkeypatch.setattr(scorer, "get_client", lambda: ai)
        monkeypatch.setattr(llm, "get_client", lambda: ai)
        monkeypatch.setattr(config, "get_llm_provider", lambda: "fake")
        return ai
    return use


def test_perfect_ai_scores_perfectly(fake):
    ai = fake(FakeAI())
    result = run.run(dataset.load(), repeats=2, batch_size=4, log=lambda *_: None)
    s = result["summary"]
    assert s["mae"] == 0 and s["exact"] == 1 and s["spearman"] == 1 and s["dealbreaker_recall"] == 1
    assert s["missing"] == 0 and result["misses"] == []
    expected = sum(-(-len(c.jobs) // 4) for c in dataset.load()) * 2
    assert result["meta"]["requests"] == len(ai.calls) == expected
    assert result["meta"]["model"] == "fake-model" and result["meta"]["prompt"] == run.prompt_version()


def test_scores_come_back_off_and_missing(fake):
    fake(FakeAI(offset=2, drop={"Shelfwise"}))
    result = run.run(dataset.load(only=["backend_engineer"]), repeats=1, log=lambda *_: None)
    s = result["summary"]
    assert s["missing"] == 1 and s["scored"] == 9 and s["bias"] > 1.5  # clamped at 10, so a little under 2
    assert s["dealbreaker_recall"] < 1 and s["dealbreakers_missed"]
    md = run.report(result)
    assert "haven't been reviewed" in md and "never came back" in md and "Biggest disagreements" in md


def test_each_candidate_is_scored_in_their_own_country(fake, monkeypatch):
    """A UK search saved on this computer mustn't turn the Australian cases into UK ones."""
    ai = fake(FakeAI())
    monkeypatch.setattr(config, "load_search_config", lambda: {"country": "uk"})
    run.run(dataset.load(only=["backend_engineer"]), repeats=1, log=lambda *_: None)
    system = ai.calls[0][0]["content"]
    assert "recruiter in Australia" in system and "recruiter in the UK" not in system
    assert config.load_search_config() == {"country": "uk"}  # put back afterwards


def test_batches_are_shuffled_per_repeat(fake):
    ai = fake(FakeAI())
    run.run(dataset.load(only=["backend_engineer"]), repeats=2, batch_size=20, log=lambda *_: None)
    orders = [re.findall(r"COMPANY: (.*)", call[-1]["content"]) for call in ai.calls]
    assert len(orders) == 2 and sorted(orders[0]) == sorted(orders[1]) and orders[0] != orders[1]


def test_a_failed_request_leaves_its_jobs_unscored(fake):
    class Flaky(FakeAI):
        def chat_json(self, messages, **kw):
            if not self.calls:
                self.calls.append(messages)
                raise RuntimeError("HTTP 503")
            return super().chat_json(messages, **kw)
    fake(Flaky())
    result = run.run(dataset.load(only=["backend_engineer"]), repeats=2, batch_size=20, log=lambda *_: None)
    assert result["meta"]["failed_requests"] == 1 and result["summary"]["missing"] == 10
    assert result["summary"]["scored"] == 10  # the second repeat still scored every job


def test_save_and_compare(fake, tmp_path):
    fake(FakeAI())
    old = run.save(run.run(dataset.load(only=["backend_engineer"]), repeats=1, log=lambda *_: None),
                   tmp_path, "before")
    fake(FakeAI(offset=-3))
    new = run.save(run.run(dataset.load(only=["backend_engineer"]), repeats=1, log=lambda *_: None),
                   tmp_path, "after")
    assert old.exists() and old.with_suffix(".md").exists() and old.name.endswith("_before.json")
    text = run.compare_files(old, new)
    assert re.search(r"mae .*\(worse\)", text) and "Jobs that moved by 2 or more" in text


def test_cli(fake, monkeypatch, tmp_path, capsys):
    assert cli.main(["--dry-run"]) == 0
    jobs = sum(len(c.jobs) for c in dataset.load())
    assert f"{jobs} jobs" in capsys.readouterr().out
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "get_llm_provider", lambda: None)
    assert cli.main(["--repeats", "1"]) == 1  # no AI set up
    fake(FakeAI())
    assert cli.main(["--repeats", "1", "--candidate", "backend_engineer", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.json"))) == 1 and "Scoring eval" in capsys.readouterr().out

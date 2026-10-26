"""Batch scoring, the free title filter, and JSON mode in the LLM client."""

import json
import re

import httpx
import pytest

from applypilot import config, database, llm
from applypilot.scoring import scorer


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    resume = tmp_path / "resume.txt"
    resume.write_text("Jane Doe. Data analyst, 4 years SQL and Power BI.")
    monkeypatch.setattr(scorer, "RESUME_PATH", resume)
    monkeypatch.setattr(scorer, "load_search_config", lambda: {"exclude_titles": ["intern", "Senior Director"]})
    monkeypatch.setattr(config, "load_search_config", lambda: {})
    conn = database.init_db(path)
    yield conn
    database.close_connection(path)


def add_jobs(conn, n, **extra):
    for i in range(n):
        cols = {"url": f"https://ex.com/{i}", "title": f"Data Analyst {i}", "company": f"Company {i}",
                "site": "indeed", "full_description": f"Role {i}: SQL, dashboards. " * 20, **extra}
        conn.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     list(cols.values()))
    conn.commit()


class FakeLLM:
    """Scores every posting id it is shown, unless told to drop or break some."""

    def __init__(self, drop=(), bad_score=(), fail_batches=()):
        self.prompts: list[str] = []
        self.drop, self.bad_score, self.fail_batches = set(drop), set(bad_score), set(fail_batches)

    def chat_json(self, messages, temperature=0.0, max_tokens=4096):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if len(self.prompts) in self.fail_batches:
            raise httpx.HTTPStatusError("HTTP 503", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(503))
        results = []
        for job_id, title in re.findall(r"### POSTING id=(\w+)\nTITLE: (.*)", prompt):
            if title in self.drop:
                continue
            score = 11 if title in self.bad_score else 7
            results.append({"id": job_id, "score": score, "keywords": ["SQL", "Power BI"],
                            "reasoning": f"fits {title}"})
        return {"results": results}


def scores(conn):
    return {r["title"]: r["fit_score"] for r in conn.execute("SELECT title, fit_score FROM jobs")}


def test_batches_cut_requests(db, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(scorer, "get_client", lambda: fake)
    add_jobs(db, 23)
    result = scorer.run_scoring(batch_size=10)
    assert result["requests"] == 3 and result["scored"] == 23 and result["errors"] == 0
    assert len(fake.prompts) == 3
    assert set(scores(db).values()) == {7}
    reasoning = db.execute("SELECT score_reasoning FROM jobs LIMIT 1").fetchone()[0]
    assert reasoning.startswith("SQL, Power BI\nfits")


def test_prompt_uses_company_not_job_board(db, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(scorer, "get_client", lambda: fake)
    add_jobs(db, 1)
    scorer.run_scoring()
    assert "COMPANY: Company 0" in fake.prompts[0]
    assert "COMPANY: indeed" not in fake.prompts[0]


def test_excluded_titles_skip_the_llm(db, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(scorer, "get_client", lambda: fake)
    add_jobs(db, 2)
    db.execute("UPDATE jobs SET title = 'Data Analyst Intern' WHERE url LIKE '%/0'")
    db.execute("UPDATE jobs SET title = 'Internal Audit Analyst' WHERE url LIKE '%/1'")  # not "intern"
    db.commit()
    result = scorer.run_scoring()
    assert result["skipped"] == 1 and result["scored"] == 1
    assert "Data Analyst Intern" not in "".join(fake.prompts)
    assert scores(db) == {"Data Analyst Intern": 0, "Internal Audit Analyst": 7}
    why = db.execute("SELECT score_reasoning FROM jobs WHERE fit_score = 0").fetchone()[0]
    assert "intern" in why and "exclude_titles" in why


def test_missing_or_invalid_scores_stay_unscored(db, monkeypatch):
    fake = FakeLLM(drop={"Data Analyst 1"}, bad_score={"Data Analyst 2"})
    monkeypatch.setattr(scorer, "get_client", lambda: fake)
    add_jobs(db, 3)
    result = scorer.run_scoring()
    assert result["errors"] == 2
    assert scores(db) == {"Data Analyst 0": 7, "Data Analyst 1": None, "Data Analyst 2": None}


def test_failed_request_leaves_jobs_for_next_run(db, monkeypatch):
    """Regression: a failed LLM call used to store score 0, so the job was never scored."""
    fake = FakeLLM(fail_batches={1})
    monkeypatch.setattr(scorer, "get_client", lambda: fake)
    add_jobs(db, 12)
    first = scorer.run_scoring(batch_size=10)
    assert first["scored"] == 2 and first["errors"] == 10
    assert list(scores(db).values()).count(None) == 10
    second = scorer.run_scoring(batch_size=10)
    assert second["scored"] == 10 and None not in scores(db).values()


def test_old_error_zeros_are_rescored(db, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(scorer, "get_client", lambda: fake)
    add_jobs(db, 1, fit_score=0, score_reasoning="\nLLM error: Client error '400 Bad Request'")
    add_jobs_excluded = {"url": "https://ex.com/skip", "title": "Old skip", "fit_score": 0,
                         "score_reasoning": "\nSkipped: title contains 'intern' (exclude_titles)",
                         "full_description": "x" * 300}
    db.execute(f"INSERT INTO jobs ({', '.join(add_jobs_excluded)}) VALUES (?, ?, ?, ?, ?)",
               list(add_jobs_excluded.values()))
    db.commit()
    assert scorer.run_scoring()["scored"] == 1
    assert scores(db) == {"Data Analyst 0": 7, "Old skip": 0}


def test_title_excluded_matching():
    assert scorer.title_excluded("Graduate Data Analyst", ["graduate"]) == "graduate"
    assert scorer.title_excluded("VP, Analytics", ["VP "]) == "VP"  # reported trimmed
    assert scorer.title_excluded("Internal Auditor", ["intern"]) is None
    assert scorer.title_excluded(None, ["intern"]) is None


# -- JSON mode in the LLM client ----------------------------------------------

def client_with(handler, base=llm._GEMINI_COMPAT_BASE):
    c = llm.LLMClient(base, "gemini-3.5-flash-lite", "k")
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_json_mode_on_openai_compatible_endpoint():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"ok": 1}\n```'}}]})

    assert client_with(handler).chat_json([{"role": "user", "content": "x"}]) == {"ok": 1}
    assert seen[0]["response_format"] == {"type": "json_object"}


def test_json_mode_on_native_gemini():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": 2}'}]}}]})

    c = client_with(handler)
    c._use_native_gemini = True
    assert c.chat_json([{"role": "user", "content": "x"}]) == {"ok": 2}
    assert seen[0]["generationConfig"]["responseMimeType"] == "application/json"


def test_json_mode_unsupported_falls_back_to_plain_text():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": 'Here: {"ok": 3}'}}]})

    c = client_with(handler, base="http://127.0.0.1:8080/v1")
    assert c.chat_json([{"role": "user", "content": "x"}]) == {"ok": 3}
    assert c.chat_json([{"role": "user", "content": "x"}]) == {"ok": 3}
    assert seen == [True, False, False]  # remembered after the first rejection


def test_bad_key_does_not_disable_json_mode():
    c = client_with(lambda req: httpx.Response(400, json={"error": {"message": "API key not valid"}}))
    with pytest.raises(httpx.HTTPStatusError):
        c.chat_json([{"role": "user", "content": "x"}])
    assert c._compat_json_ok is True

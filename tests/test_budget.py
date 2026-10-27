"""Daily Gemini request budget (improvement D)."""

from datetime import datetime, timezone

import httpx
import pytest

from applypilot import budget, database, llm
from applypilot.apply import local_engine as le

MODEL = "gemini-3.5-flash-lite"


@pytest.fixture(autouse=True)
def limits(monkeypatch):
    monkeypatch.setenv("GEMINI_DAILY_LIMIT", "10")
    monkeypatch.setenv("GEMINI_APPLY_RESERVE", "3")
    database.init_db()


def test_quota_day_is_pacific():
    # 06:00 UTC on 27 Oct is still 26 Oct in California (UTC-7 in daylight time)
    assert budget.quota_day(datetime(2026, 10, 27, 6, tzinfo=timezone.utc)) == "2026-10-26"
    assert budget.quota_day(datetime(2026, 10, 27, 8, tzinfo=timezone.utc)) == "2026-10-27"
    # Winter: UTC-8
    assert budget.quota_day(datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc)) == "2026-01-14"


def test_pipeline_keeps_reserve_for_applying():
    budget.record(MODEL, 7)
    assert budget.remaining(MODEL, budget.PIPELINE) == 0
    assert budget.remaining(MODEL, budget.APPLY) == 3
    with pytest.raises(budget.BudgetExhausted, match="kept for applying"):
        budget.check(MODEL, budget.PIPELINE)
    budget.check(MODEL, budget.APPLY)  # applying may still go ahead
    budget.record(MODEL, 3)
    with pytest.raises(budget.BudgetExhausted, match="Daily Gemini limit reached"):
        budget.check(MODEL, budget.APPLY)


def test_other_providers_are_not_budgeted():
    budget.record("gpt-4o-mini", 99)
    budget.check("gpt-4o-mini")
    assert budget.status("gpt-4o-mini") is None


def _client(handler):
    c = llm.LLMClient("https://generativelanguage.googleapis.com/v1beta/openai", MODEL, "k")
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def _ok(request):
    return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})


def test_llm_client_counts_and_stops():
    client = _client(_ok)
    for _ in range(7):
        assert client.ask("x") == "hi"
    assert budget.used(MODEL) == 7
    with pytest.raises(budget.BudgetExhausted):
        client.ask("x")
    assert budget.used(MODEL) == 7  # the refused call never reached Google


def test_daily_quota_429_stops_without_retrying(monkeypatch):
    calls = []

    def quota(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "Quota exceeded for metric: "
                                                   "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}})

    monkeypatch.setattr(llm.time, "sleep", lambda s: pytest.fail("retried a daily quota error"))
    with pytest.raises(budget.BudgetExhausted):
        _client(quota).ask("x")
    assert calls == [1]
    assert budget.status(MODEL)["left"] == 0  # Google's word beats our count


def test_scoring_stops_cleanly_and_leaves_jobs_for_next_run(tmp_path, monkeypatch):
    from applypilot.scoring import scorer
    conn = database.get_connection()
    for i in range(3):
        conn.execute("INSERT INTO jobs (url, title, site, full_description) VALUES (?, ?, 'x', 'desc')",
                     (f"u{i}", f"Analyst {i}"))
    conn.commit()
    (tmp_path / "resume.txt").write_text("resume")
    monkeypatch.setattr(scorer, "RESUME_PATH", tmp_path / "resume.txt")
    monkeypatch.setattr(scorer, "load_search_config", lambda: {})

    def no_budget(*args):
        raise budget.BudgetExhausted("out of requests")

    monkeypatch.setattr(scorer, "score_batch", no_budget)
    scorer.run_scoring(batch_size=1)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NULL").fetchone()[0] == 3


def test_local_engine_counts_against_full_limit(monkeypatch):
    budget.record(MODEL, 7)  # pipeline is out; applying has 3 left

    def handler(request):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": '{"actions": [], "needs_attention": []}'}]}}]})

    real = httpx.Client
    monkeypatch.setattr(le.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    for _ in range(3):
        le._call_ai("p", "k", MODEL)
    with pytest.raises(budget.BudgetExhausted):
        le._call_ai("p", "k", MODEL)
    assert budget.used(MODEL) == 10

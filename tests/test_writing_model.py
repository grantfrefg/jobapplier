"""The writing model (resumes, cover letters) and its fallback to the main model."""

import httpx
import pytest

from applypilot import config, llm

DAILY_QUOTA = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded", "details": [
    {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}]
MINUTE_QUOTA = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded", "details": [
    {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}]


def ok(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def make_clients(responses: dict[str, list]):
    """Writing (gemini-3.8-flash) + main (lite) clients answering from per-model queues."""
    calls: list[str] = []

    def handler(request):
        model = __import__("json").loads(request.content)["model"]
        calls.append(model)
        return responses[model].pop(0)

    transport = httpx.MockTransport(handler)
    main = llm.LLMClient(llm._GEMINI_COMPAT_BASE, "gemini-3.5-flash-lite", "k")
    writing = llm.LLMClient(llm._GEMINI_COMPAT_BASE, "gemini-3.8-flash", "k", max_retries=1)
    main._client = writing._client = httpx.Client(transport=transport)
    return llm.FallbackClient(writing, main), calls


def test_uses_writing_model_when_available():
    client, calls = make_clients({"gemini-3.8-flash": [ok("great resume")]})
    assert client.ask("write") == "great resume"
    assert calls == ["gemini-3.8-flash"]


def test_daily_quota_switches_to_main_model_for_rest_of_run():
    client, calls = make_clients({
        "gemini-3.8-flash": [httpx.Response(429, json=DAILY_QUOTA)],
        "gemini-3.5-flash-lite": [ok("first"), ok("second")],
    })
    assert client.ask("one") == "first"
    assert client.ask("two") == "second"
    # One request to discover the quota is gone (no retries, no backoff), then never again
    assert calls == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]
    assert client.model == "gemini-3.5-flash-lite"


def test_per_minute_limit_falls_back_only_for_that_request():
    client, calls = make_clients({
        "gemini-3.8-flash": [httpx.Response(429, json=MINUTE_QUOTA), ok("writing again")],
        "gemini-3.5-flash-lite": [ok("fallback")],
    })
    assert client.ask("one") == "fallback"
    assert client.ask("two") == "writing again"
    assert calls == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]


def test_unknown_writing_model_falls_back_for_good():
    client, calls = make_clients({
        "gemini-3.8-flash": [httpx.Response(404, json=[{"error": {"message": "model not found"}}])],
        "gemini-3.5-flash-lite": [ok("a"), ok("b")],
    })
    assert [client.ask("1"), client.ask("2")] == ["a", "b"]
    assert calls.count("gemini-3.8-flash") == 1


def test_bad_key_is_not_hidden_by_fallback():
    client, _ = make_clients({"gemini-3.8-flash": [
        httpx.Response(400, json=[{"error": {"message": "Please pass a valid API key"}}])]})
    with pytest.raises(httpx.HTTPStatusError, match="valid API key"):
        client.ask("x")


@pytest.fixture
def fresh_clients(monkeypatch):
    monkeypatch.setattr(llm, "_instance", None)
    monkeypatch.setattr(llm, "_writing_instance", None)
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL", "LLM_MODEL", "LLM_WRITING_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_get_client_writing_defaults_on_gemini(fresh_clients):
    fresh_clients.setenv("GEMINI_API_KEY", "k")
    main, writing = llm.get_client(), llm.get_client("writing")
    assert main.model == config.DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert isinstance(writing, llm.FallbackClient)
    assert writing.preferred.model == config.DEFAULT_GEMINI_WRITING_MODEL == "gemini-3.8-flash"
    assert writing.preferred.max_retries == 1 and writing.fallback is main
    assert llm.get_client("writing") is writing  # shared, so the "quota gone" state sticks


def test_get_client_writing_override_and_same_model(fresh_clients):
    fresh_clients.setenv("GEMINI_API_KEY", "k")
    fresh_clients.setenv("LLM_WRITING_MODEL", "gemini-3.5-flash-lite")  # same as main -> plain client
    assert llm.get_client("writing") is llm.get_client()


def test_get_client_writing_non_gemini_uses_main_model(fresh_clients):
    fresh_clients.setenv("OPENAI_API_KEY", "k")
    assert llm.get_client("writing") is llm.get_client()


def test_tailor_and_cover_use_writing_client_but_judge_does_not():
    """Only the text recruiters read goes to the writing model."""
    from pathlib import Path
    src = Path(llm.__file__).parent / "scoring"
    tailor = (src / "tailor.py").read_text()
    assert tailor.count('get_client("writing")') == 1
    judge = tailor[tailor.index("def judge_tailored_resume"):tailor.index("def tailor_resume")]
    assert "get_client()" in judge and '"writing"' not in judge
    assert 'get_client("writing")' in (src / "cover_letter.py").read_text()

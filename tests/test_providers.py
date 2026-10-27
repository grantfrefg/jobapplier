"""Your own paid AI keys (OpenAI, Anthropic) and the Codex apply engine."""

import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from applypilot import config, llm

KEYS = ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_URL")


@pytest.fixture
def env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)

    def set_env(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
    return set_env


def test_provider_follows_the_choice_and_falls_back_to_the_key_you_have(env):
    assert config.get_llm_provider() is None
    env(ANTHROPIC_API_KEY="sk-ant-1")
    assert config.get_llm_provider() == "anthropic" and config.get_llm_model() == "claude-opus-5-5"
    env(GEMINI_API_KEY="AIza-1")
    assert config.get_llm_provider() == "gemini"  # free key first when nothing is chosen
    env(LLM_PROVIDER="anthropic")
    assert config.get_llm_provider() == "anthropic"
    env(LLM_PROVIDER="openai")  # chosen, but no OpenAI key: use what's there
    assert config.get_llm_provider() == "gemini"


def test_model_setting_only_applies_to_its_own_provider(env):
    env(OPENAI_API_KEY="sk-1", LLM_PROVIDER="openai", LLM_MODEL="gemini-3.5-flash")
    assert config.get_llm_model() == "gpt-5-mini"  # a Gemini name is never sent to OpenAI
    env(LLM_MODEL="gpt-5")
    assert config.get_llm_model() == "gpt-5"
    env(ANTHROPIC_API_KEY="sk-ant", LLM_PROVIDER="anthropic", LLM_MODEL="claude-sonnet-5-5")
    assert config.get_llm_model() == "claude-sonnet-5-5"
    assert llm._detect_provider() == ("https://api.anthropic.com", "claude-sonnet-5-5", "sk-ant")


@pytest.mark.parametrize("model, expected", [
    ("gpt-5-mini", {"max_completion_tokens": 9000, "reasoning_effort": "low"}),
    ("o4-mini", {"max_completion_tokens": 9000, "reasoning_effort": "low"}),
    ("gpt-4.1-mini", {"max_completion_tokens": 1000, "temperature": 0.2}),
])
def test_openai_request_fields(model, expected):
    payload = {"model": model, "messages": [], "temperature": 0.2, "max_tokens": 1000}
    llm._openai_params(payload, model)
    assert {k: v for k, v in payload.items() if k not in ("model", "messages")} == expected


class FakeAnthropic:
    """Stands in for the SDK client: records the request, returns a canned reply."""

    def __init__(self, stop_reason="end_turn", text='{"ok": true}'):
        self.sent = None
        reply = SimpleNamespace(stop_reason=stop_reason,
                                content=[SimpleNamespace(type="thinking", thinking=""),
                                         SimpleNamespace(type="text", text=text)])

        def create(**kwargs):
            self.sent = kwargs
            return reply
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))

    def close(self):
        pass


def _claude(model, fake):
    pytest.importorskip("anthropic")
    from applypilot.llm_anthropic import ClaudeChat
    chat = ClaudeChat("sk-ant-test", model)
    chat._client = fake
    return chat


def test_claude_request_shape():
    fake = FakeAnthropic()
    chat = _claude("claude-opus-5-5", fake)
    text = chat.chat([{"role": "system", "content": "You score jobs."}, {"role": "user", "content": "Job..."}],
                     max_tokens=300, json_mode=True)
    assert text == '{"ok": true}'  # thinking blocks skipped
    sent = fake.sent
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == 16000  # room for thinking
    assert sent["system"].startswith("You score jobs.") and "JSON only" in sent["system"]
    assert sent["messages"] == [{"role": "user", "content": "Job..."}]
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert "temperature" not in sent  # sampling parameters are rejected by current models


def test_claude_older_model_and_refusal():
    from applypilot.llm_anthropic import AnthropicError
    fake = FakeAnthropic()
    _claude("claude-haiku-4-5", fake).chat([{"role": "user", "content": "x"}])
    assert "fallbacks" not in fake.sent and "output_config" not in fake.sent
    with pytest.raises(AnthropicError, match="declined"):
        _claude("claude-opus-5-5", FakeAnthropic(stop_reason="refusal")).chat([{"role": "user", "content": "x"}])


def test_llm_client_hands_claude_requests_to_the_sdk(monkeypatch):
    pytest.importorskip("anthropic")
    fake = FakeAnthropic(text="hello")
    client = llm.LLMClient("https://api.anthropic.com", "claude-opus-5-5", "sk-ant-test")
    client._claude._client = fake
    assert client.chat([{"role": "user", "content": "hi"}]) == "hello"


def test_form_ai_uses_the_chosen_provider(env, monkeypatch):
    from applypilot.apply import local_engine as le
    env(ANTHROPIC_API_KEY="sk-ant", LLM_PROVIDER="anthropic")
    sent = {}

    class Client:
        def chat(self, messages, **kw):
            sent.update(messages=messages, **kw)
            return '{"actions": [{"id": "f1", "action": "fill", "value": "{{EMAIL}}"}]}'

    monkeypatch.setattr(llm, "get_client", lambda: Client())
    monkeypatch.setattr(le, "_call_gemini_api", lambda *a: pytest.fail("Gemini isn't the provider"))
    result = le._call_ai("FORM FIELDS: ...", "sk-ant", "claude-opus-5-5")
    assert result["actions"][0]["value"] == "{{EMAIL}}"
    assert sent["messages"][0] == {"role": "system", "content": le._SYSTEM_PROMPT} and sent["json_mode"]


# --- Codex engine -------------------------------------------------------------

def test_codex_command_is_browser_only_and_reads_stdin(tmp_path):
    from applypilot.apply import launcher
    cmd = launcher.codex_command(9222, tmp_path / "last.txt", "gpt-5-codex")
    assert cmd[1] == "exec" and cmd[-1] == "-"
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    args = [c for c in cmd if c.startswith("mcp_servers.playwright.args=")][0]
    assert "--cdp-endpoint=http://localhost:9222" in json.loads(args.split("=", 1)[1])
    assert cmd[cmd.index("--model") + 1] == "gpt-5-codex"


def _fake_codex(tmp_path, events, last_message):
    """A `codex` on PATH that prints these events and writes the last message."""
    script = tmp_path / "bin" / "codex"
    script.parent.mkdir()
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"for e in {events!r}: print(json.dumps(e))\n"
        "out = sys.argv[sys.argv.index('--output-last-message') + 1]\n"
        f"open(out, 'w').write({last_message!r})\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script.parent


@pytest.mark.skipif(os.name == "nt", reason="shell-script stand-in for the codex CLI")
@pytest.mark.parametrize("events, last, expected", [
    ([{"type": "thread.started"},
      {"type": "item.started", "item": {"type": "mcp_tool_call", "tool": "browser_navigate",
                                        "arguments": {"url": "https://jobs.example/apply"}}},
      {"type": "item.completed", "item": {"type": "agent_message", "text": "Filled and submitted."}}],
     "RESULT:APPLIED", "applied"),
    ([{"type": "error", "message": "Reconnecting... 2/5 (unexpected status 401 Unauthorized)"},
      {"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized"}}],
     "", "failed:codex_not_signed_in"),
])
def test_codex_run(tmp_path, monkeypatch, events, last, expected):
    from applypilot.apply import launcher
    monkeypatch.setenv("PATH", str(_fake_codex(tmp_path, events, last)) + os.pathsep + os.environ["PATH"])
    for name, value in {"LOG_DIR": tmp_path, "APPLY_WORKER_DIR": tmp_path / "aw",
                        "PROFILE_PATH": tmp_path / "profile.json"}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(launcher.prompt_mod, "build_prompt", lambda **kw: "apply please")
    launcher.init_worker(0)
    job = {"url": "https://jobs.example/1", "title": "Engineer", "site": "linkedin"}
    status, _ = launcher._run_codex(job, port=9222, worker_id=0, dry_run=False)
    assert status == expected
    if expected == "applied":
        log = (tmp_path / "worker-0.log").read_text()
        assert ">> navigate https://jobs.example/apply" in log and "Reconnecting" not in log

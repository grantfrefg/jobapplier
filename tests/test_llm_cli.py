"""Your ChatGPT or Claude subscription as the AI, through its CLI (llm_cli.py)."""

import json
import os
import stat
import sys

import pytest

from applypilot import budget, config, llm, llm_cli

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Python-script stand-ins for the CLIs")


def _fake_cli(tmp_path, name, body):
    """A stand-in CLI on PATH. It saves its arguments, stdin and working folder, then runs `body`."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    script = folder / name
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "data = sys.stdin.read()\n"
        f"json.dump({{'args': sys.argv[1:], 'stdin': data, 'cwd': os.getcwd(), 'files': os.listdir('.')}},"
        f" open({str(tmp_path / 'seen.json')!r}, 'w'))\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return folder


@pytest.fixture
def on_path(monkeypatch):
    def add(folder):
        monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ["PATH"])
    return add


MESSAGES = [{"role": "system", "content": "You score jobs."}, {"role": "user", "content": "Job: Engineer"}]


def test_codex_subagent_gets_the_step_and_no_tools(tmp_path, on_path):
    on_path(_fake_cli(tmp_path, "codex",
                      "out = sys.argv[sys.argv.index('--output-last-message') + 1]\n"
                      "open(out, 'w').write('{\"score\": 8}')\n"))
    reply = llm_cli.SubscriptionChat(config.PROVIDER_CODEX_CLI, "gpt-5-codex").chat(MESSAGES, json_mode=True)
    assert json.loads(reply) == {"score": 8}
    seen = json.loads((tmp_path / "seen.json").read_text())
    args = seen["args"]
    assert args[0] == "exec" and args[-1] == "-" and args[args.index("--sandbox") + 1] == "read-only"
    assert "mcp_servers={}" in args and "--ephemeral" in args and args[args.index("--model") + 1] == "gpt-5-codex"
    assert "== YOUR INSTRUCTIONS ==\nYou score jobs." in seen["stdin"] and "Job: Engineer" in seen["stdin"]
    assert "JSON object only" in seen["stdin"]
    assert seen["files"] == [] and "applypilot-ai-" in seen["cwd"]  # an empty folder: nothing to read


def test_codex_usage_limit_stops_the_run(tmp_path, on_path):
    on_path(_fake_cli(tmp_path, "codex",
                      "print(json.dumps({'type': 'error', 'message': \"You've hit your usage limit. Try again in "
                      "3 hours.\"}))\n"))
    with pytest.raises(budget.BudgetExhausted, match="ChatGPT plan's usage limit"):
        llm_cli.SubscriptionChat(config.PROVIDER_CODEX_CLI).chat(MESSAGES)


def test_claude_subagent_and_its_errors(tmp_path, on_path):
    result = {"type": "result", "subtype": "success", "is_error": False, "result": "OK"}
    on_path(_fake_cli(tmp_path, "claude", f"print(json.dumps({result!r}))\n"))
    assert llm_cli.SubscriptionChat(config.PROVIDER_CLAUDE_CLI).chat(MESSAGES) == "OK"
    args = json.loads((tmp_path / "seen.json").read_text())["args"]
    assert args[:3] == ["-p", "--output-format", "json"] and args[args.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args and "--model" not in args
    assert len(args[args.index("--system-prompt") + 1]) < 400  # the step's instructions go on stdin

    signed_out = {"type": "result", "subtype": "success", "is_error": True,
                  "result": "Invalid API key · Please run /login"}
    on_path(_fake_cli(tmp_path, "claude", f"print(json.dumps({signed_out!r}))\n"))
    with pytest.raises(llm_cli.SubscriptionError, match="isn't signed in: run `claude`"):
        llm_cli.SubscriptionChat(config.PROVIDER_CLAUDE_CLI).chat(MESSAGES)

    limited = {**signed_out, "result": "Claude AI usage limit reached|1761800000"}
    on_path(_fake_cli(tmp_path, "claude", f"print(json.dumps({limited!r}))\n"))
    with pytest.raises(budget.BudgetExhausted, match="Claude plan's usage limit"):
        llm_cli.SubscriptionChat(config.PROVIDER_CLAUDE_CLI).chat(MESSAGES)


def test_choosing_a_subscription(tmp_path, on_path, monkeypatch):
    for key in ("LLM_PROVIDER", "LLM_MODEL", "CODEX_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "LLM_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_tool_dirs", lambda: [])
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("LLM_PROVIDER", "codex_cli")
    assert config.get_llm_provider() is None  # chosen, but the CLI isn't installed
    on_path(_fake_cli(tmp_path, "codex",
                      "out = sys.argv[sys.argv.index('--output-last-message') + 1]\nopen(out, 'w').write('OK')\n"))
    assert config.get_llm_provider() == "codex_cli" and config.provider_key("codex_cli") == ""
    assert config.get_llm_model("codex_cli") == ""  # Codex's own default
    monkeypatch.setenv("CODEX_MODEL", "gpt-5-codex")
    assert config.get_llm_model("codex_cli") == "gpt-5-codex"
    client = llm.LLMClient(*llm._detect_provider())
    assert client.chat(MESSAGES) == "OK" and client.provider_name == "ChatGPT subscription (Codex)"
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    assert config.get_llm_provider() == "codex_cli"  # your choice wins over a key you also have


def test_setup_with_a_subscription(tmp_path, on_path, monkeypatch):
    from fastapi.testclient import TestClient

    from applypilot import database
    from applypilot.ui.server import create_app
    for name, value in {"APP_DIR": tmp_path, "ENV_PATH": tmp_path / ".env", "LOG_DIR": tmp_path / "logs",
                        "PROFILE_PATH": tmp_path / "profile.json", "RESUME_PATH": tmp_path / "r.txt"}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "load_env", lambda: None)
    for key in ("LLM_PROVIDER", "LLM_MODEL", "CODEX_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "LLM_URL"):
        monkeypatch.setenv(key, "")  # set (not deleted), so the values the API writes are undone afterwards
    monkeypatch.setattr(config, "_tool_dirs", lambda: [])
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with TestClient(create_app()) as client:
        client.post("/api/settings", json={"LLM_PROVIDER": "claude_cli"})
        r = client.post("/api/settings/test-ai")
        assert r.status_code == 400 and "claude CLI wasn't found" in r.json()["detail"]
        assert client.get("/api/status").json()["checks"]["claude_cli"] is False

        ok = {"type": "result", "subtype": "success", "is_error": False, "result": "OK"}
        on_path(_fake_cli(tmp_path, "claude", f"print(json.dumps({ok!r}))\n"))
        status = client.get("/api/status").json()
        assert status["provider"] == "claude_cli" and status["checks"]["llm_key"] and status["checks"]["claude_cli"]
        assert status["provider_name"] == "Claude subscription (via Claude Code)"
        r = client.post("/api/settings/test-ai").json()
        assert r["ok"] and r["provider"] == "claude_cli"
    database.close_connection(tmp_path / "t.db")

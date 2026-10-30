"""Your ChatGPT or Claude subscription as the AI, through its own CLI.

Each request runs the CLI once as a single-purpose subagent: the step's own
instructions (scorer, tailor, fact-check, cover letter, form answers) go in on
stdin, tools are off, and the reply comes back as text (JSON when asked). It
uses the plan the CLI is signed in with (`codex login`, or `claude` then /login),
so there's no API key or API bill, but it shares that plan's usage limits.

- codex_cli:  `codex exec`, read-only sandbox, no MCP servers, in an empty folder
- claude_cli: `claude -p`, no tools, no MCP servers, no saved session
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from applypilot import config

TIMEOUT = 600  # seconds for one request (a long tailoring reply can take a few minutes)
# A short system prompt on the command line; the step's own instructions go in on stdin,
# which keeps long prompts clear of Windows' command-line limit (npm's .cmd shims run via cmd.exe)
_SUBAGENT_ROLE = ("You are one specialised step inside ApplyPilot, a job application tool. The message gives "
                  "your instructions and data. Follow them exactly, use no tools, and reply with only what they "
                  "ask for.")
_LIMIT = re.compile(r"usage limit|limit reached|rate limit|quota|too many requests|try again (later|in|at)", re.I)
_SIGNED_OUT = re.compile(r"401|unauthori[sz]ed|not logged in|log ?in|sign ?in|invalid api key|authenticat", re.I)
NAMES = {config.PROVIDER_CODEX_CLI: "ChatGPT subscription (Codex)",
         config.PROVIDER_CLAUDE_CLI: "Claude subscription (Claude Code)"}
SIGN_IN = {config.PROVIDER_CODEX_CLI: "run `codex login` in a terminal",
           config.PROVIDER_CLAUDE_CLI: "run `claude` in a terminal and type /login"}


class SubscriptionError(RuntimeError):
    """The CLI couldn't answer (not signed in, not installed, or a bad reply)."""


def build_input(messages: list[dict], json_mode: bool) -> str:
    """OpenAI-style messages as one text for the CLI: the instructions, then the data."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
    parts = [f"== YOUR INSTRUCTIONS ==\n{system}"] if system else []
    parts.append(f"== INPUT ==\n{rest}")
    if json_mode:
        parts.append("Reply with the JSON object only: no prose, no code fences.")
    return "\n\n".join(parts)


def command(provider: str, model: str, out_file: Path | None = None) -> list[str]:
    cli = config.agent_cli(config.SUBSCRIPTION_CLIS[provider])
    if not cli:
        raise SubscriptionError(f"The {config.SUBSCRIPTION_CLIS[provider]} CLI isn't installed (Setup shows how)")
    if provider == config.PROVIDER_CODEX_CLI:
        cmd = [cli, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
               "-c", "approval_policy='never'", "-c", "mcp_servers={}",
               "--output-last-message", str(out_file)]
        return cmd + (["--model", model] if model else []) + ["-"]
    cmd = [cli, "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence", "--system-prompt", _SUBAGENT_ROLE]
    return cmd + (["--model", model] if model else [])


def _limit_error(provider: str, text: str):
    from applypilot.budget import BudgetExhausted
    plan = "ChatGPT" if provider == config.PROVIDER_CODEX_CLI else "Claude"
    hint = " ".join(text.split())[:200]
    return BudgetExhausted(f"Your {plan} plan's usage limit is reached ({hint}). The rest waits for the next run: "
                           "try again when your plan's limit resets, or choose another AI in Setup.")


def _failure(provider: str, text: str) -> Exception:
    if _LIMIT.search(text):
        return _limit_error(provider, text)
    if _SIGNED_OUT.search(text):
        return SubscriptionError(f"{NAMES[provider]} isn't signed in: {SIGN_IN[provider]}, then try again.")
    return SubscriptionError(f"{NAMES[provider]} didn't answer: {' '.join(text.split())[:300]}")


class SubscriptionChat:
    def __init__(self, provider: str, model: str = "", timeout: int = TIMEOUT) -> None:
        if provider not in config.SUBSCRIPTION_CLIS:
            raise ValueError(f"not a subscription provider: {provider}")
        self.provider, self.model, self.timeout = provider, model, timeout

    def chat(self, messages: list[dict], max_tokens: int = 4096, json_mode: bool = False) -> str:
        text = build_input(messages, json_mode)
        # An empty folder: the subagent has nothing on disk to read or change
        with tempfile.TemporaryDirectory(prefix="applypilot-ai-") as work:
            out_file = Path(work) / "reply.txt"
            cmd = command(self.provider, self.model, out_file)
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                proc = subprocess.run(cmd, input=text, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", cwd=work, timeout=self.timeout, creationflags=flags,
                                      env={**os.environ, "NO_COLOR": "1"})
            except subprocess.TimeoutExpired as e:
                raise SubscriptionError(f"{NAMES[self.provider]} took longer than {self.timeout // 60} minutes") from e
            if self.provider == config.PROVIDER_CODEX_CLI:
                return self._codex_reply(proc, out_file)
            return self._claude_reply(proc)

    def _codex_reply(self, proc: subprocess.CompletedProcess, out_file: Path) -> str:
        reply = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else ""
        if reply:
            return reply
        errors = []
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") in ("error", "turn.failed"):
                errors.append(str(event.get("message") or (event.get("error") or {}).get("message") or ""))
        raise _failure(self.provider, " ".join(errors) or proc.stderr or f"exit code {proc.returncode}")

    def _claude_reply(self, proc: subprocess.CompletedProcess) -> str:
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise _failure(self.provider, proc.stderr or proc.stdout or f"exit code {proc.returncode}") from None
        result = str(data.get("result") or "")
        if data.get("is_error") or data.get("subtype", "success") != "success" or not result.strip():
            raise _failure(self.provider, result or str(data.get("subtype") or "") or proc.stderr)
        return result.strip()

    def close(self) -> None:
        pass

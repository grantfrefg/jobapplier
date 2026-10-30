"""Claude (Anthropic API) as the AI provider, through the official `anthropic` SDK.

Used by llm.LLMClient when the provider is "anthropic" (ANTHROPIC_API_KEY).
The default model is Claude Opus 5.5; Settings can name another (e.g.
claude-sonnet-5-5 or claude-haiku-5-5, which cost less per job).
"""

from __future__ import annotations

import re

# Models that take the server-side refusal fallback: if a safety classifier
# declines the request, the API re-runs it on another model in the same call.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that take output_config.effort: Opus 4.5+, Sonnet 4.6+, and the 5 family
# (Haiku 4.5 and Sonnet 4.5 reject it)
_EFFORT_MODELS = re.compile(r"^claude-(opus-(5|4-[5-9])|sonnet-(5|4-[6-9])|haiku-5|fable-5|mythos-5)")
# Thinking counts toward max_tokens, so leave room beyond the answer itself
_MIN_MAX_TOKENS = 16000


class AnthropicError(RuntimeError):
    """A Claude request failed in a way retrying won't fix (bad key, bad model, refusal)."""


class ClaudeChat:
    def __init__(self, api_key: str, model: str, timeout: float = 300.0) -> None:
        import anthropic

        self._anthropic = anthropic
        # The SDK retries 408/409/429/5xx and connection errors with backoff
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=5, timeout=timeout)
        self.model = model

    def chat(self, messages: list[dict], max_tokens: int = 4096, json_mode: bool = False) -> str:
        """OpenAI-style messages in, the reply text out."""
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if json_mode:
            system += "\n\nReply with the JSON only: no prose, no code fences."
        convo = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        kwargs: dict = {"model": self.model, "max_tokens": max(max_tokens, _MIN_MAX_TOKENS), "messages": convo}
        if system.strip():
            kwargs["system"] = system.strip()
        if _EFFORT_MODELS.match(self.model):
            kwargs["output_config"] = {"effort": "medium"}

        a = self._anthropic
        try:
            if self.model in _FALLBACK_MODELS:
                response = self._client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                response = self._client.messages.create(**kwargs)
        except a.AuthenticationError as e:
            raise AnthropicError("Claude API key not valid (Anthropic said: authentication failed)") from e
        except a.PermissionDeniedError as e:
            raise AnthropicError(f"Claude API key can't use this model ({self.model}): {e.message}") from e
        except a.NotFoundError as e:
            raise AnthropicError(f"Claude model not found: {self.model}") from e
        except a.BadRequestError as e:
            if "credit balance" in str(e.message).lower():
                from applypilot.budget import BudgetExhausted
                raise BudgetExhausted("Anthropic says this API account has no credit left. Add credit at "
                                      "console.anthropic.com/settings/billing, then try again. A Claude Pro or "
                                      "Max subscription doesn't include API credit.") from e
            raise AnthropicError(f"Claude rejected the request: {e.message}") from e
        except a.RateLimitError as e:
            raise AnthropicError("Claude rate limit: still limited after several retries. New Anthropic accounts "
                                 "start with low limits that rise as you spend; wait a minute and try again.") from e

        if response.stop_reason == "refusal":
            raise AnthropicError("Claude declined this request (safety refusal)")
        return "".join(block.text for block in response.content if block.type == "text")

    def close(self) -> None:
        self._client.close()

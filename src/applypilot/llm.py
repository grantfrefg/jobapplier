"""
Unified LLM client for ApplyPilot.

The provider is chosen in Settings (LLM_PROVIDER) or by which key is set
(config.get_llm_provider):
  GEMINI_API_KEY    -> Google Gemini (default: gemini-3.5-flash-lite, free tier)
  OPENAI_API_KEY    -> OpenAI (default: gpt-5-mini)
  ANTHROPIC_API_KEY -> Anthropic Claude (default: claude-opus-5-5; llm_anthropic.py)
  LLM_URL           -> Local llama.cpp / Ollama compatible endpoint

LLM_MODEL overrides the model when it names one of the chosen provider's models.
"""

import logging
import os
import re
import time

import httpx

from applypilot import budget, config
from applypilot.config import DEFAULT_GEMINI_MODEL  # noqa: F401  (re-exported for callers)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

_BASE_URLS = {
    config.PROVIDER_GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai",
    config.PROVIDER_OPENAI: "https://api.openai.com/v1",
    config.PROVIDER_ANTHROPIC: "https://api.anthropic.com",
}


def _detect_provider() -> tuple[str, str, str]:
    """Return (base_url, model, api_key) for the chosen provider.

    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.
    """
    provider = config.get_llm_provider()
    if provider is None:
        raise RuntimeError(
            "No AI provider configured. Set GEMINI_API_KEY (free), OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY or LLM_URL (Setup page or .env)."
        )
    model = config.get_llm_model(provider)
    if provider == config.PROVIDER_LOCAL:
        return config.provider_key(provider).rstrip("/"), model, os.environ.get("LLM_API_KEY", "")
    return _BASE_URLS[provider], model, config.provider_key(provider)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_TIMEOUT = 120  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10


_RATE_LIMIT_TIPS = {
    "Gemini": "Gemini's free tier allows about 15 requests a minute",
    "OpenAI": ("new OpenAI accounts start with low limits that rise as you spend "
               "(platform.openai.com/settings/organization/limits)"),
}


def _duration_seconds(value: str | None) -> float | None:
    """Seconds from a Retry-After or OpenAI reset header ("12", "1s", "6m0s", "20ms")."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
    if not parts:
        return None
    scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * scale[unit] for n, unit in parts)


def no_credit_message(provider: str, body: str) -> str | None:
    """The reason when a 429 means the account has no API credit (retrying won't help)."""
    text = (body or "").lower()
    if provider == "OpenAI" and ("insufficient_quota" in text or "exceeded your current quota" in text):
        return ("OpenAI says this API account has no credit left (insufficient_quota). Add a payment method or "
                "prepaid credit at platform.openai.com/settings/organization/billing, then try again. A ChatGPT "
                "Plus or Pro subscription doesn't include API credit.")
    return None


_GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"


class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 403 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.
    """

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)
        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        # False once the OpenAI-compat endpoint has rejected response_format
        self._compat_json_ok: bool = True
        self._is_gemini: bool = base_url.startswith(_GEMINI_COMPAT_BASE)
        self.provider_name: str = ("Gemini" if self._is_gemini else "OpenAI" if "api.openai.com" in base_url
                                   else "The AI provider")
        self._is_openai: bool = base_url.startswith("https://api.openai.com")
        self._claude = None
        if base_url.startswith("https://api.anthropic.com"):
            from applypilot.llm_anthropic import ClaudeChat
            self._claude = ClaudeChat(api_key, model)

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 403,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{_GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            params={"key": self.api_key},
        )
        _raise_for_status(resp)
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        """Call the OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._is_openai:
            _openai_params(payload, self.model)
        if json_mode and self._compat_json_ok:
            payload["response_format"] = {"type": "json_object"}

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        if resp.status_code == 400 and "response_format" in payload:
            # Some local servers don't support JSON mode: ask in plain text
            # (the prompt still asks for JSON). Only remember that if the plain
            # request works -- a 400 can also mean a bad key.
            del payload["response_format"]
            resp = self._client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            if resp.status_code != 400:
                log.warning("Endpoint doesn't support JSON mode; continuing without it.")
                self._compat_json_ok = False

        # 403 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code == 403 and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        return self._handle_compat_response(resp)

    @staticmethod
    def _handle_compat_response(resp: httpx.Response) -> str:
        _raise_for_status(resp)
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Send a chat completion request and return the assistant message text.

        json_mode asks the provider for a JSON-only reply (Gemini's
        responseMimeType / OpenAI's response_format); see chat_json().
        """
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        if self._claude is not None:
            return self._claude.chat(messages, max_tokens=max_tokens, json_mode=json_mode)

        # Pipeline work stops cleanly when the day's Gemini budget is spent
        budget.check(self.model if self._is_gemini else None, budget.PIPELINE)
        text = self._chat_with_retries(messages, temperature, max_tokens, json_mode)
        if self._is_gemini:
            budget.record(self.model)
        return text

    def _chat_with_retries(self, messages: list[dict], temperature: float, max_tokens: int,
                           json_mode: bool) -> str:
        for attempt in range(_MAX_RETRIES):
            try:
                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    return self._chat_native_gemini(messages, temperature, max_tokens, json_mode)

                return self._chat_compat(messages, temperature, max_tokens, json_mode)

            except _GeminiCompatForbidden as exc:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned 403 for model '%s'. "
                    "Switching to native generateContent API. "
                    "(Preview/experimental models are often compat-only on native.)",
                    self.model,
                )
                self._use_native_gemini = True
                # Retry immediately with native — don't count as a rate-limit wait
                try:
                    return self._chat_native_gemini(messages, temperature, max_tokens, json_mode)
                except httpx.HTTPStatusError as native_exc:
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: 403 Forbidden. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                if self._is_gemini and budget.is_daily_quota_error(resp.status_code, resp.text):
                    raise budget.exhausted_error(self.model) from exc  # retrying today won't help
                no_credit = resp.status_code == 429 and no_credit_message(self.provider_name, resp.text)
                if no_credit:  # stops the run cleanly, like a used-up budget
                    raise budget.BudgetExhausted(no_credit) from exc
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                    # The provider's own reset time if it sent one (Retry-After; OpenAI's x-ratelimit-reset-*)
                    hinted = [_duration_seconds(resp.headers.get(h)) for h in
                              ("Retry-After", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")]
                    hinted = [h for h in hinted if h is not None]
                    wait = min(max(hinted) if hinted else _RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    tip = _RATE_LIMIT_TIPS.get(self.provider_name)
                    log.warning(
                        "%s rate limit (HTTP %s): waiting %.0fs before retry %d/%d.%s",
                        self.provider_name, resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                        f" Tip: {tip}." if tip else "",
                    )
                    time.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "%s request timed out, retrying in %ds (attempt %d/%d)",
                        self.provider_name, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def chat_json(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 4096):
        """chat() in JSON mode, parsed. Raises ValueError if the reply isn't JSON."""
        return parse_json(self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True))

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()
        if self._claude is not None:
            self._claude.close()


# OpenAI reasoning models (gpt-5 family, o-series) take max_completion_tokens,
# only the default temperature, and spend part of the token limit on reasoning.
_OPENAI_REASONING = re.compile(r"^(gpt-5|o\d)")


def _openai_params(payload: dict, model: str) -> None:
    """Adjust a chat/completions payload for api.openai.com's current models."""
    tokens = payload.pop("max_tokens")
    if _OPENAI_REASONING.match(model.lower()):
        payload.pop("temperature", None)
        payload["reasoning_effort"] = "low"
        tokens += 8000  # room for the reasoning before the answer
    payload["max_completion_tokens"] = tokens


def error_detail(resp: httpx.Response) -> str:
    """The provider's error message from a failed response, if it sent one."""
    try:
        data = resp.json()
        err = (data[0] if isinstance(data, list) and data else data).get("error", {})
        return err.get("message", "") if isinstance(err, dict) else str(err)
    except Exception:
        return resp.text[:200]


def _raise_for_status(resp: httpx.Response) -> None:
    """raise_for_status, but include the provider's own error message.

    Gemini answers a bad key with a bare "400 Bad Request" status; the
    reason ("Please pass a valid API key") is only in the body.
    """
    if resp.is_success:
        return
    detail = error_detail(resp)
    message = f"HTTP {resp.status_code} from {resp.request.url.host}"
    if detail:
        message += f": {detail}"
    raise httpx.HTTPStatusError(message, request=resp.request, response=resp)


def parse_json(text: str):
    """Parse a model reply as JSON, tolerating ```json fences or stray prose around it."""
    import json
    import re
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Model reply was not JSON: {text[:200]!r}")


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 403. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat 403: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  model: %s", base_url, model)
        _instance = LLMClient(base_url, model, api_key)
    return _instance

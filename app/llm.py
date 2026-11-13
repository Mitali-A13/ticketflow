"""LLM layer. get_llm() picks the provider from env. FakeLLM is for tests/demos.

Every LLM object has:  complete(system: str, user: str) -> LLMResponse
and raises RetryableError or FatalError so the worker knows what to do.
"""

import json
import os
import re
import time
from dataclasses import dataclass

import httpx

from app.errors import FatalError, RetryableError


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int


PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "default_model": "openai/gpt-oss-120b",
    },
}


class OpenAICompatLLM:
    """Talks to any OpenAI-style /chat/completions endpoint (Groq is one)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        extra_body: dict | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_body = extra_body or {}
        self.client = client or httpx.Client(timeout=timeout)

    def complete(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        payload.update(self.extra_body)
        start = time.perf_counter()
        try:
            r = self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.TimeoutException as e:  # must come before TransportError
            raise RetryableError(f"timeout: {type(e).__name__}") from e
        except httpx.TransportError as e:
            raise RetryableError(f"network error: {type(e).__name__}: {e}") from e
        latency_ms = int((time.perf_counter() - start) * 1000)

        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:  # 400, 401, 403, 404...: waiting never fixes these
            raise FatalError(f"HTTP {r.status_code}: {r.text[:200]}")

        try:
            text = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise RetryableError(f"unexpected response shape: {r.text[:200]}") from e
        if not text or not text.strip():
            raise RetryableError("empty model output")
        return LLMResponse(text=text, model=self.model, latency_ms=latency_ms)


# ---------------------------------------------------------------- fake model
class FakeLLM:
    """No network, no key. Keyword guesses, only to test the plumbing.
    Magic words in the email force failures:
      FAKE_MALFORMED -> returns non-JSON    FAKE_RATE_LIMIT -> retryable error
      FAKE_BAD_KEY   -> fatal error
    """

    model = "fake-llm"

    KEYWORDS = (
        ("refund", ("refund", "money back")),
        ("billing", ("invoice", "charged", "billing", "payment")),
        ("shipping", ("order", "delivery", "shipping", "arrived", "tracking")),
        ("account", ("password", "account", "login")),
        ("technical", ("error", "crash", "bug", "not working", "stopped working")),
    )

    def complete(self, system: str, user: str) -> LLMResponse:
        if "FAKE_MALFORMED" in user:
            return LLMResponse("this is not json", self.model, 1)
        if "FAKE_RATE_LIMIT" in user:
            raise RetryableError("fake HTTP 429")
        if "FAKE_BAD_KEY" in user:
            raise FatalError("fake HTTP 401")
        lower = user.lower()
        category = "other"
        for name, words in self.KEYWORDS:
            if any(w in lower for w in words):
                category = name
                break
        match = re.search(r"\b[A-Z]{2,5}-\d{3,}\b", user)
        data = {
            "category": category,
            "urgency": "high" if ("urgent" in lower or "asap" in lower) else "medium",
            "customer_name": None,
            "order_id": match.group(0) if match else None,
            "summary": "Fake summary of the customer's email.",
            "suggested_reply": "Thanks for contacting us. We are looking into it.",
            "confidence": 0.5 if category == "other" else 0.9,
        }
        return LLMResponse(json.dumps(data), self.model, 1)


# ------------------------------------------------------------------ factory
def env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def get_llm():
    """FAKE_LLM=true -> FakeLLM. Otherwise the provider named by LLM_PROVIDER."""
    if env_true("FAKE_LLM"):
        return FakeLLM()
    provider = os.environ.get("LLM_PROVIDER", "groq").strip().lower()
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise FatalError(
            f"unknown LLM_PROVIDER '{provider}' (supported: {', '.join(PROVIDERS)})"
        )
    api_key = os.environ.get(cfg["key_env"], "").strip()
    if not api_key:
        raise FatalError(f"{cfg['key_env']} is not set")
    model = os.environ.get("LLM_MODEL", "").strip() or cfg["default_model"]
    extra = {}
    if provider == "groq" and model.startswith("openai/gpt-oss"):
        extra["reasoning_effort"] = "low"  # keeps the reasoning model fast and cheap
    return OpenAICompatLLM(cfg["base_url"], api_key, model, extra_body=extra)

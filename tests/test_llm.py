import json

import httpx
import pytest

from app.errors import FatalError, RetryableError
from app.extract import parse_json_object, validate_extraction
from app.llm import FakeLLM, OpenAICompatLLM, get_llm


def make_llm(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatLLM("https://example.test/v1", "secret", "m1", client=client)


def reply(status, **kwargs):
    return lambda request: httpx.Response(status, **kwargs)


def test_success_returns_text_and_model():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"a": 1}'}}]}
        )

    resp = make_llm(handler).complete("sys", "usr")
    assert resp.text == '{"a": 1}' and resp.model == "m1" and resp.latency_ms >= 0
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_429_is_retryable():
    with pytest.raises(RetryableError, match="429"):
        make_llm(reply(429, text="slow down")).complete("s", "u")


def test_500_is_retryable():
    with pytest.raises(RetryableError, match="500"):
        make_llm(reply(500, text="oops")).complete("s", "u")


def test_401_is_fatal():
    with pytest.raises(FatalError, match="401"):
        make_llm(reply(401, text="bad key")).complete("s", "u")


def test_400_is_fatal():
    with pytest.raises(FatalError, match="400"):
        make_llm(reply(400, text="bad request")).complete("s", "u")


def test_timeout_is_retryable():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(RetryableError, match="timeout"):
        make_llm(handler).complete("s", "u")


def test_connection_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("no network", request=request)

    with pytest.raises(RetryableError, match="network"):
        make_llm(handler).complete("s", "u")


def test_empty_output_is_retryable():
    body = {"choices": [{"message": {"content": ""}}]}
    with pytest.raises(RetryableError, match="empty"):
        make_llm(reply(200, json=body)).complete("s", "u")


def test_fake_flag_gives_fake_llm(monkeypatch):
    monkeypatch.setenv("FAKE_LLM", "true")
    assert isinstance(get_llm(), FakeLLM)


def test_missing_api_key_is_fatal(monkeypatch):
    monkeypatch.delenv("FAKE_LLM", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    with pytest.raises(FatalError, match="GROQ_API_KEY"):
        get_llm()


def test_unknown_provider_is_fatal(monkeypatch):
    monkeypatch.delenv("FAKE_LLM", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(FatalError, match="unknown"):
        get_llm()


def test_fake_llm_output_passes_validation():
    user = "Subject: Refund please\n<email>\nI want my money back for order ABC-1234\n</email>"
    ex = validate_extraction(parse_json_object(FakeLLM().complete("sys", user).text))
    assert ex.category == "refund" and ex.order_id == "ABC-1234"

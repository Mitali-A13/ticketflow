import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.llm import LLMResponse
from app.queue import enqueue_email

TEST_KEY = "test-key-123"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A fresh database file per test."""
    monkeypatch.setenv("TICKETFLOW_DB", str(tmp_path / "test.db"))
    c = connect()
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def make_job(conn):
    def _make(key="k1", body="hello", now=1000.0):
        job_id, _ = enqueue_email(conn, key, "a@x.com", "subject", body, now=now)
        return job_id

    return _make


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("TICKETFLOW_API_KEY", TEST_KEY)
    return TEST_KEY


@pytest.fixture
def client(conn, api_key):
    """A test client that already sends the right API key."""
    from app.main import app

    with TestClient(app) as c:
        c.headers.update({"X-API-Key": api_key})
        yield c


@pytest.fixture
def anon_client(conn, api_key):
    """A test client with no key and no cookie."""
    from app.main import app

    with TestClient(app) as c:
        yield c


class ScriptedLLM:
    """A fake model that returns/raises a prepared list of things, in order."""

    model = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model=self.model, latency_ms=5)


@pytest.fixture
def scripted():
    return ScriptedLLM


@pytest.fixture
def good_json():
    """Build a valid model answer as JSON text. Override any field by keyword."""

    def _make(**overrides):
        data = {
            "category": "shipping",
            "urgency": "medium",
            "customer_name": "Asha",
            "order_id": "ORD-1001",
            "summary": "Customer asks where their order is.",
            "suggested_reply": "Hi Asha, we are checking on ORD-1001.",
            "confidence": 0.9,
        }
        data.update(overrides)
        return json.dumps(data)

    return _make

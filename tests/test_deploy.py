import time

from fastapi.testclient import TestClient

from app.deploy import seed_demo_data, start_embedded_worker
from app.extract import make_processor
from app.llm import FakeLLM
from app.worker import process_one


def statuses(conn):
    return [r["status"] for r in conn.execute("SELECT status FROM jobs").fetchall()]


def drain(conn):
    processor = make_processor(FakeLLM())
    while process_one(conn, processor=processor) is not None:
        pass


def test_seed_creates_five_jobs_once(conn):
    assert seed_demo_data(conn) == 5
    assert seed_demo_data(conn) == 0
    assert len(statuses(conn)) == 5


def test_seeded_jobs_are_processed_and_one_needs_review(conn):
    seed_demo_data(conn)
    drain(conn)
    found = statuses(conn)
    assert set(found) <= {"succeeded", "needs_review"}
    assert "needs_review" in found


def test_embedded_worker_processes_jobs(conn, monkeypatch):
    monkeypatch.setenv("FAKE_LLM", "true")
    seed_demo_data(conn)
    stop = start_embedded_worker()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and any(
            s in ("queued", "running") for s in statuses(conn)
        ):
            time.sleep(0.1)
        assert set(statuses(conn)) <= {"succeeded", "needs_review"}
    finally:
        stop.set()


def test_login_page_shows_demo_key_when_configured(anon_client, monkeypatch):
    monkeypatch.setenv("DEMO_KEY_HINT", "demo-key-xyz")
    page = anon_client.get("/review/login").text
    assert "demo-key-xyz" in page and "Public demo" in page


def test_login_page_hides_key_by_default(anon_client, monkeypatch):
    monkeypatch.delenv("DEMO_KEY_HINT", raising=False)
    assert "Public demo" not in anon_client.get("/review/login").text


def test_demo_banner_is_shown_with_the_fake_model(client, monkeypatch):
    monkeypatch.setenv("FAKE_LLM", "true")
    assert "fake keyword model" in client.get("/dashboard").text


def test_no_demo_banner_with_a_real_model(client, monkeypatch):
    monkeypatch.delenv("FAKE_LLM", raising=False)
    assert "fake keyword model" not in client.get("/dashboard").text


def test_startup_seeds_and_processes_everything(conn, api_key, monkeypatch):
    monkeypatch.setenv("SEED_DEMO", "true")
    monkeypatch.setenv("EMBEDDED_WORKER", "true")
    monkeypatch.setenv("FAKE_LLM", "true")
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1000")
    from app.main import app

    with TestClient(app) as c:
        c.headers.update({"X-API-Key": api_key})
        deadline = time.time() + 10
        m = c.get("/metrics").json()
        while (
            time.time() < deadline
            and m["counts"]["queued"] + m["counts"]["running"] > 0
        ):
            time.sleep(0.2)
            m = c.get("/metrics").json()
        assert m["total"] == 5 and m["counts"]["dead"] == 0
        assert m["counts"]["succeeded"] + m["counts"]["needs_review"] == 5

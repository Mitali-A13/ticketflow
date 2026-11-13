from app.extract import make_processor
from app.llm import FakeLLM
from app.worker import process_one

H = {"Idempotency-Key": "key-1"}
BODY = {
    "sender": "a@x.com",
    "subject": "Where is my order?",
    "body": "Order 123 is late",
}


def test_new_email_returns_202(client):
    r = client.post("/emails", json=BODY, headers=H)
    assert r.status_code == 202
    assert r.json() == {"job_id": 1, "status": "queued", "duplicate": False}


def test_same_key_returns_200_and_same_job(client):
    first = client.post("/emails", json=BODY, headers=H)
    second = client.post("/emails", json=BODY, headers=H)
    assert second.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["duplicate"] is True


def test_missing_idempotency_key_is_422(client):
    assert client.post("/emails", json=BODY).status_code == 422


def test_empty_body_is_422(client):
    bad = {**BODY, "body": ""}
    assert client.post("/emails", json=bad, headers=H).status_code == 422


def test_get_job(client):
    job_id = client.post("/emails", json=BODY, headers=H).json()["job_id"]
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    data = r.json()
    assert (
        data["status"] == "queued"
        and data["attempts"] == 0
        and data["last_error"] is None
    )


def test_get_unknown_job_is_404(client):
    assert client.get("/jobs/999").status_code == 404


def test_queued_job_has_no_result_yet(client):
    job_id = client.post("/emails", json=BODY, headers=H).json()["job_id"]
    assert client.get(f"/jobs/{job_id}").json()["result"] is None


def test_finished_job_includes_result(client, conn):
    email = {
        "sender": "a@x.com",
        "subject": "Late order",
        "body": "Order ORD-123 has not arrived",
    }
    job_id = client.post("/emails", json=email, headers=H).json()["job_id"]
    assert process_one(conn, processor=make_processor(FakeLLM())) == "succeeded"
    data = client.get(f"/jobs/{job_id}").json()
    assert data["status"] == "succeeded"
    assert data["result"]["category"] == "shipping"
    assert data["result"]["order_id"] == "ORD-123"
    assert data["result"]["model"] == "fake-llm"
    assert "raw_llm_output" not in data["result"]

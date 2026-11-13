from app.queue import claim_job, fail_job

BODY = {"sender": "a@x.com", "subject": "Hi", "body": "Order 123 is late"}


def post(client, key):
    return client.post("/emails", json=BODY, headers={"Idempotency-Key": key})


def kill_job(conn, job_id):
    claim_job(conn)
    fail_job(conn, job_id, "boom", retryable=False)


def test_metrics_endpoint_shape(client):
    post(client, "a")
    post(client, "b")
    assert client.get("/metrics").json() == {
        "counts": {
            "queued": 2,
            "running": 0,
            "succeeded": 0,
            "needs_review": 0,
            "dead": 0,
        },
        "total": 2,
        "queue_depth": 2,
        "pending_review": 0,
        "avg_attempts": None,
    }


def test_list_jobs_by_status(client, conn):
    post(client, "a")
    post(client, "b")
    kill_job(conn, 1)
    dead = client.get("/jobs", params={"status": "dead"}).json()
    assert [j["id"] for j in dead] == [1]
    assert len(client.get("/jobs").json()) == 2


def test_list_jobs_bad_status_is_422(client):
    assert client.get("/jobs", params={"status": "nonsense"}).status_code == 422


def test_retry_endpoint_requeues_dead_job(client, conn):
    post(client, "a")
    kill_job(conn, 1)
    r = client.post("/jobs/1/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "queued" and r.json()["attempts"] == 0


def test_retry_non_dead_job_is_409(client):
    post(client, "a")
    assert client.post("/jobs/1/retry").status_code == 409


def test_retry_unknown_job_is_404(client):
    assert client.post("/jobs/999/retry").status_code == 404

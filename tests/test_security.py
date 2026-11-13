from app.ratelimit import RateLimiter

BODY = {"sender": "a@x.com", "subject": "Hi", "body": "Order 123 is late"}


def test_missing_key_is_401(anon_client):
    r = anon_client.post("/emails", json=BODY, headers={"Idempotency-Key": "k"})
    assert r.status_code == 401


def test_wrong_key_is_401(anon_client):
    r = anon_client.post(
        "/emails", json=BODY, headers={"Idempotency-Key": "k", "X-API-Key": "wrong"}
    )
    assert r.status_code == 401


def test_health_needs_no_key(anon_client):
    assert anon_client.get("/health").json() == {"status": "ok"}


def test_rate_limit_returns_429_with_retry_after(client):
    client.app.state.limiter = RateLimiter(limit=2, window=60.0)
    assert client.get("/metrics").status_code == 200
    assert client.get("/metrics").status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_server_without_configured_key_rejects_everything(client, monkeypatch):
    monkeypatch.delenv("TICKETFLOW_API_KEY")
    assert client.get("/metrics").status_code == 401  # fails closed


def test_every_protected_route_requires_key(anon_client):
    for method, path in [
        ("GET", "/metrics"),
        ("GET", "/jobs"),
        ("GET", "/jobs/1"),
        ("POST", "/jobs/1/retry"),
    ]:
        assert anon_client.request(method, path).status_code == 401, path

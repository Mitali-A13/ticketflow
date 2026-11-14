from app.extract import make_processor
from app.llm import FakeLLM
from app.queue import get_job, get_result
from app.worker import process_one


def submit(client, form_id="a" * 32, **overrides):
    data = {
        "form_id": form_id,
        "sender": "a@x.com",
        "subject": "Late order",
        "body": "Where is order ORD-123? Thanks, Asha",
    }
    data.update(overrides)
    return client.post("/demo/submit", data=data, follow_redirects=False)


def job_count(conn):
    return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_root_redirects_to_dashboard(anon_client):
    r = anon_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"


def test_demo_requires_login(anon_client):
    r = anon_client.get("/demo", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review/login"


def test_demo_home_renders_form(client):
    r = client.get("/demo")
    assert r.status_code == 200
    assert 'name="form_id"' in r.text and 'name="body"' in r.text


def test_sample_prefills_the_form(client):
    r = client.get("/demo?sample=2")
    assert r.status_code == 200 and "Charged twice" in r.text and "ORD-77102" in r.text


def test_submit_creates_job_and_redirects(client, conn):
    r = submit(client)
    assert r.status_code == 303 and r.headers["location"] == "/demo/jobs/1"
    assert get_job(conn, 1)["status"] == "queued"


def test_double_submit_gives_one_job(client, conn):
    first = submit(client)
    second = submit(client)
    assert first.headers["location"] == second.headers["location"] == "/demo/jobs/1"
    assert job_count(conn) == 1


def test_submit_with_empty_body_is_rejected(client, conn):
    r = submit(client, body="   ")
    assert r.status_code == 400 and "Please fill in" in r.text
    assert job_count(conn) == 0


def test_queued_job_page_polls_for_updates(client):
    submit(client)
    page = client.get("/demo/jobs/1").text
    assert 'data-live="on"' in page and "Queued" in page


def test_finished_job_page_shows_result(client, conn):
    submit(client)
    assert process_one(conn, processor=make_processor(FakeLLM())) == "succeeded"
    page = client.get("/demo/jobs/1").text
    assert 'data-live="off"' in page
    assert "Auto-accepted" in page and "shipping" in page and "ORD-123" in page


def test_flagged_job_links_to_review(client, conn, scripted, good_json):
    submit(client)
    llm = scripted([good_json(confidence=0.3)])
    assert process_one(conn, processor=make_processor(llm)) == "needs_review"
    page = client.get("/demo/jobs/1").text
    assert "Needs human review" in page and 'href="/review"' in page


def test_job_page_shows_review_outcome(client, conn, scripted, good_json):
    submit(client)
    llm = scripted([good_json(confidence=0.3)])
    process_one(conn, processor=make_processor(llm))
    result_id = get_result(conn, 1)["id"]
    assert (
        client.post(
            f"/review/{result_id}", data={"action": "approved"}, follow_redirects=False
        ).status_code
        == 303
    )
    page = client.get("/demo/jobs/1").text
    assert "Reviewed by a human: approved" in page
    assert "Needs human review" not in page


def test_dead_job_can_be_retried_from_the_demo(client, conn):
    submit(client, body="FAKE_BAD_KEY please")
    assert process_one(conn, processor=make_processor(FakeLLM())) == "dead"
    assert 'action="/demo/jobs/1/retry"' in client.get("/demo/jobs/1").text
    r = client.post("/demo/jobs/1/retry", follow_redirects=False)
    assert r.status_code == 303
    job = get_job(conn, 1)
    assert job["status"] == "queued" and job["attempts"] == 0


def test_retry_of_a_job_that_is_not_dead_is_409(client):
    submit(client)
    assert client.post("/demo/jobs/1/retry", follow_redirects=False).status_code == 409


def test_job_page_escapes_html(client):
    submit(client, body="<script>alert(1)</script> Order ORD-123")
    page = client.get("/demo/jobs/1").text
    assert "&lt;script&gt;" in page and "<script>" not in page


def test_unknown_job_is_404(client):
    assert client.get("/demo/jobs/999").status_code == 404

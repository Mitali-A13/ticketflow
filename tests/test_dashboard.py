from app.extract import make_processor
from app.llm import FakeLLM
from app.queue import get_result
from app.worker import process_one


def add(
    client, form_id, subject="Late order", body="Where is order ORD-123? Thanks, Asha"
):
    return client.post(
        "/demo/submit",
        data={
            "form_id": form_id,
            "sender": "a@x.com",
            "subject": subject,
            "body": body,
        },
        follow_redirects=False,
    )


def login(c, key):
    return c.post("/review/login", data={"key": key}, follow_redirects=False)


def test_dashboard_requires_login(anon_client):
    r = anon_client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review/login"


def test_dashboard_empty_state(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "No emails yet" in r.text


def test_dashboard_lists_job_and_polls_while_in_progress(client):
    add(client, "a" * 32)
    page = client.get("/dashboard").text
    assert "Late order" in page and 'data-live="on"' in page and "Queued" in page


def test_dashboard_stops_polling_when_idle(client, conn):
    add(client, "a" * 32)
    process_one(conn, processor=make_processor(FakeLLM()))
    page = client.get("/dashboard").text
    assert 'data-live="off"' in page and "Auto-accepted" in page and "shipping" in page


def test_review_view_lists_only_flagged_jobs(client, conn, scripted, good_json):
    add(client, "a" * 32, subject="Flagged one")
    add(client, "b" * 32, subject="Fine one")
    process_one(conn, processor=make_processor(scripted([good_json(confidence=0.3)])))
    process_one(conn, processor=make_processor(FakeLLM()))
    page = client.get("/dashboard?view=review").text
    assert "Flagged one" in page and "Fine one" not in page


def test_reviewed_jobs_move_to_the_reviewed_view(client, conn, scripted, good_json):
    add(client, "a" * 32, subject="Flagged one")
    process_one(conn, processor=make_processor(scripted([good_json(confidence=0.3)])))
    result_id = get_result(conn, 1)["id"]
    client.post(
        f"/review/{result_id}", data={"action": "approved"}, follow_redirects=False
    )
    assert 'class="status reviewed"' in client.get("/dashboard").text
    assert "Flagged one" in client.get("/dashboard?view=reviewed").text
    assert "Flagged one" not in client.get("/dashboard?view=review").text


def test_unknown_view_falls_back_to_all(client):
    r = client.get("/dashboard?view=bogus")
    assert r.status_code == 200 and "All emails" in r.text


def test_static_css_needs_no_login(anon_client):
    r = anon_client.get("/static/style.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"]


def test_login_lands_on_dashboard(anon_client, api_key):
    r = login(anon_client, api_key)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"


def test_logout_clears_the_session(anon_client, api_key):
    login(anon_client, api_key)
    assert anon_client.get("/dashboard").status_code == 200
    r = anon_client.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review/login"
    after = anon_client.get("/dashboard", follow_redirects=False)
    assert after.status_code == 303 and after.headers["location"] == "/review/login"


def test_job_page_shows_progress_steps(client, conn):
    add(client, "a" * 32)
    process_one(conn, processor=make_processor(FakeLLM()))
    page = client.get("/demo/jobs/1").text
    assert "Human review" in page and "Not needed" in page


def test_sidebar_shows_pending_review_count(client, conn, scripted, good_json):
    add(client, "a" * 32)
    process_one(conn, processor=make_processor(scripted([good_json(confidence=0.3)])))
    assert 'class="count">1<' in client.get("/dashboard").text

import json

from app.extract import make_processor
from app.queue import get_result
from app.reviews import EDITABLE
from app.worker import process_one

BODY = "Hi, where is order ORD-1001? Regards, Asha"


def flagged(conn, make_job, scripted, good_json, body=BODY, **overrides):
    """Create one result that landed in needs_review (confidence 0.3 by default)."""
    job_id = make_job(body=body)
    llm = scripted([good_json(**{"confidence": 0.3, **overrides})])
    assert (
        process_one(conn, processor=make_processor(llm), now=1000.0) == "needs_review"
    )
    return get_result(conn, job_id)


def form_from(stored, **changes):
    form = {k: ("" if stored[k] is None else str(stored[k])) for k in EDITABLE}
    form.update(changes)
    return form


def review_rows(conn):
    return conn.execute("SELECT * FROM reviews").fetchall()


def test_review_page_lists_pending_item(client, conn, make_job, scripted, good_json):
    flagged(conn, make_job, scripted, good_json)
    r = client.get("/review")
    assert r.status_code == 200
    assert "1 waiting" in r.text and "where is order" in r.text


def test_review_page_escapes_html(client, conn, make_job, scripted, good_json):
    flagged(conn, make_job, scripted, good_json, body="<script>alert(1)</script> help")
    r = client.get("/review")
    assert "&lt;script&gt;" in r.text and "<script>" not in r.text


def test_approve_records_review_and_clears_queue(
    client, conn, make_job, scripted, good_json
):
    stored = flagged(conn, make_job, scripted, good_json)
    r = client.post(
        f"/review/{stored['id']}", data={"action": "approved"}, follow_redirects=False
    )
    assert r.status_code == 303
    row = review_rows(conn)[0]
    assert row["reviewer_action"] == "approved" and row["corrected_fields"] is None
    assert row["result_id"] == stored["id"]
    assert "Nothing to review" in client.get("/review").text


def test_correct_stores_only_changed_fields(
    client, conn, make_job, scripted, good_json
):
    stored = flagged(conn, make_job, scripted, good_json)
    data = {**form_from(stored, category="refund"), "action": "corrected"}
    r = client.post(f"/review/{stored['id']}", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert json.loads(review_rows(conn)[0]["corrected_fields"]) == {
        "category": "refund"
    }
    assert get_result(conn, stored["job_id"])["category"] == "shipping"  # original kept


def test_correct_with_no_changes_is_rejected(
    client, conn, make_job, scripted, good_json
):
    stored = flagged(conn, make_job, scripted, good_json)
    data = {**form_from(stored), "action": "corrected"}
    r = client.post(f"/review/{stored['id']}", data=data, follow_redirects=False)
    assert r.status_code == 400 and "changed nothing" in r.text
    assert review_rows(conn) == []


def test_invalid_category_is_rejected(client, conn, make_job, scripted, good_json):
    stored = flagged(conn, make_job, scripted, good_json)
    data = {**form_from(stored, category="spam"), "action": "corrected"}
    r = client.post(f"/review/{stored['id']}", data=data, follow_redirects=False)
    assert r.status_code == 400 and review_rows(conn) == []


def test_cannot_review_twice(client, conn, make_job, scripted, good_json):
    stored = flagged(conn, make_job, scripted, good_json)
    url = f"/review/{stored['id']}"
    assert (
        client.post(
            url, data={"action": "approved"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        client.post(
            url, data={"action": "approved"}, follow_redirects=False
        ).status_code
        == 409
    )
    assert len(review_rows(conn)) == 1


def test_cannot_review_auto_accepted_result(
    client, conn, make_job, scripted, good_json
):
    job_id = make_job(body=BODY)
    llm = scripted([good_json()])
    assert process_one(conn, processor=make_processor(llm), now=1000.0) == "succeeded"
    result_id = get_result(conn, job_id)["id"]
    r = client.post(
        f"/review/{result_id}", data={"action": "approved"}, follow_redirects=False
    )
    assert r.status_code == 409 and review_rows(conn) == []


def test_review_unknown_result_is_404(client):
    r = client.post("/review/999", data={"action": "approved"}, follow_redirects=False)
    assert r.status_code == 404


def test_review_without_login_redirects_to_login(anon_client):
    r = anon_client.get("/review", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/review/login"


def test_login_with_right_key_sets_cookie_and_allows_access(anon_client, api_key):
    r = anon_client.post("/review/login", data={"key": api_key}, follow_redirects=False)
    assert r.status_code == 303 and "ticketflow_key" in r.headers["set-cookie"]
    assert anon_client.get("/review").status_code == 200


def test_login_with_wrong_key_is_401(anon_client):
    r = anon_client.post("/review/login", data={"key": "wrong"}, follow_redirects=False)
    assert r.status_code == 401 and "set-cookie" not in r.headers

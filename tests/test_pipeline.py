import json

from app.errors import FatalError, RetryableError
from app.extract import make_processor
from app.queue import get_job, get_result
from app.worker import process_one

BODY = "Hi, where is order ORD-1001? Regards, Asha"


def run(conn, llm, now):
    return process_one(conn, processor=make_processor(llm), now=now, rng=lambda: 0)


def test_good_output_is_saved_and_job_succeeds(conn, make_job, scripted, good_json):
    job_id = make_job(body=BODY)
    raw = good_json()
    assert run(conn, scripted([raw]), 1000.0) == "succeeded"
    assert get_job(conn, job_id)["status"] == "succeeded"
    r = get_result(conn, job_id)
    assert r["category"] == "shipping" and r["order_id"] == "ORD-1001"
    assert r["customer_name"] == "Asha" and r["confidence"] == 0.9
    assert r["model"] == "scripted" and r["latency_ms"] == 5
    assert r["raw_llm_output"] == raw


def test_review_case_is_saved_as_needs_review(conn, make_job, scripted, good_json):
    job_id = make_job(body=BODY)
    assert run(conn, scripted([good_json(confidence=0.3)]), 1000.0) == "needs_review"
    assert get_job(conn, job_id)["status"] == "needs_review"
    assert get_result(conn, job_id)["confidence"] == 0.3


def test_invented_order_id_saved_as_null(conn, make_job, scripted, good_json):
    job_id = make_job(body=BODY)
    assert (
        run(conn, scripted([good_json(order_id="ORD-9999")]), 1000.0) == "needs_review"
    )
    assert get_result(conn, job_id)["order_id"] is None


def test_malformed_output_retried_then_succeeds(conn, make_job, scripted, good_json):
    job_id = make_job(body=BODY)
    llm = scripted(["Sorry, I cannot do that.", good_json()])
    assert run(conn, llm, 1000.0) == "queued"
    job = get_job(conn, job_id)
    assert job["last_error"].startswith("retryable: ") and job["next_run_at"] == 1004.0
    assert run(conn, llm, 1003.0) is None  # still waiting
    assert run(conn, llm, 1004.0) == "succeeded"
    assert get_job(conn, job_id)["attempts"] == 2 and len(llm.calls) == 2


def test_llm_rate_limit_is_retried(conn, make_job, scripted, good_json):
    job_id = make_job(body=BODY)
    llm = scripted([RetryableError("HTTP 429: slow down"), good_json()])
    assert run(conn, llm, 1000.0) == "queued"
    assert "429" in get_job(conn, job_id)["last_error"]
    assert run(conn, llm, 1004.0) == "succeeded"


def test_llm_fatal_error_goes_dead_at_once(conn, make_job, scripted):
    job_id = make_job(body=BODY)
    assert run(conn, scripted([FatalError("HTTP 401: bad key")]), 1000.0) == "dead"
    job = get_job(conn, job_id)
    assert job["attempts"] == 1 and job["last_error"] == "fatal: HTTP 401: bad key"


def test_schema_invalid_forever_goes_dead_and_saves_no_result(conn, make_job, scripted):
    job_id = make_job(body=BODY)
    bad = json.dumps({"category": "nonsense"})
    llm = scripted([bad, bad, bad, bad])
    outcomes = [run(conn, llm, t) for t in (1000.0, 2000.0, 3000.0, 4000.0)]
    assert outcomes == ["queued", "queued", "queued", "dead"]
    assert "schema invalid" in get_job(conn, job_id)["last_error"]
    assert get_result(conn, job_id) is None

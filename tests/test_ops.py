from app.ops import get_metrics, list_jobs, retry_job
from app.queue import claim_job, complete_job, fail_job, get_job


def test_metrics_empty(conn):
    m = get_metrics(conn)
    assert m["counts"] == {
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "needs_review": 0,
        "dead": 0,
    }
    assert m["total"] == 0 and m["queue_depth"] == 0 and m["pending_review"] == 0
    assert m["avg_attempts"] is None


def test_metrics_counts(conn, make_job):
    a, b, _c = make_job("a"), make_job("b"), make_job("c")
    claim_job(conn, now=1000.0)  # claims a
    complete_job(conn, a, now=1001.0)
    claim_job(conn, now=1000.0)  # claims b
    fail_job(conn, b, "bad", retryable=False, now=1000.0)  # dead
    m = get_metrics(conn)
    assert m["counts"] == {
        "queued": 1,
        "running": 0,
        "succeeded": 1,
        "needs_review": 0,
        "dead": 1,
    }
    assert m["total"] == 3 and m["queue_depth"] == 1
    assert m["avg_attempts"] == 1.0  # only tried jobs count


def test_list_jobs_filters_by_status(conn, make_job):
    a, b = make_job("a"), make_job("b")
    claim_job(conn, now=1000.0)
    fail_job(conn, a, "bad", retryable=False, now=1000.0)
    assert [j["id"] for j in list_jobs(conn, "dead")] == [a]
    assert [j["id"] for j in list_jobs(conn)] == [b, a]  # newest first


def test_retry_dead_job_requeues_with_fresh_attempts(conn, make_job):
    job_id = make_job()
    claim_job(conn, now=1000.0)
    fail_job(conn, job_id, "boom", retryable=False, now=1000.0)
    assert retry_job(conn, job_id, now=2000.0) == "queued"
    job = get_job(conn, job_id)
    assert job["status"] == "queued" and job["attempts"] == 0
    assert job["next_run_at"] == 2000.0 and job["last_error"] == "boom"
    assert claim_job(conn, now=2000.0)["attempts"] == 1


def test_retry_refuses_a_job_that_is_not_dead(conn, make_job):
    job_id = make_job()
    assert retry_job(conn, job_id, now=2000.0) == "not_dead"


def test_retry_unknown_job(conn):
    assert retry_job(conn, 999, now=2000.0) == "not_found"

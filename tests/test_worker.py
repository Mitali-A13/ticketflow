from app.queue import get_job
from app.worker import process_one


def test_success(conn, make_job):
    job_id = make_job(body="normal email")
    assert process_one(conn, now=1000.0) == "succeeded"
    assert get_job(conn, job_id)["status"] == "succeeded"
    assert process_one(conn, now=1000.0) is None  # nothing left


def test_retry_once_then_success(conn, make_job):
    job_id = make_job(body="FAKE_RETRY_ONCE")
    assert process_one(conn, now=1000.0, rng=lambda: 0) == "queued"
    assert get_job(conn, job_id)["next_run_at"] == 1004.0
    assert process_one(conn, now=1003.0) is None  # still waiting
    assert process_one(conn, now=1004.0) == "succeeded"
    assert get_job(conn, job_id)["attempts"] == 2


def test_fatal_goes_dead_at_once(conn, make_job):
    job_id = make_job(body="FAKE_FATAL")
    assert process_one(conn, now=1000.0) == "dead"
    job = get_job(conn, job_id)
    assert job["attempts"] == 1 and "fatal" in job["last_error"]


def test_always_failing_job_ends_dead_after_4_attempts(conn, make_job):
    job_id = make_job(body="FAKE_RETRY_ALWAYS")
    outcomes = [
        process_one(conn, now=t, rng=lambda: 0)
        for t in (1000.0, 2000.0, 3000.0, 4000.0)
    ]
    assert outcomes == ["queued", "queued", "queued", "dead"]
    assert get_job(conn, job_id)["last_error"].startswith("retryable")


def test_unexpected_exception_is_retried(conn, make_job):
    job_id = make_job()

    def buggy(job, email):
        raise ValueError("oops")

    assert process_one(conn, processor=buggy, now=1000.0) == "queued"
    assert "ValueError" in get_job(conn, job_id)["last_error"]

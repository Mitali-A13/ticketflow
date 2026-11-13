import threading

from app.db import connect
from app.queue import (
    backoff_delay,
    claim_job,
    complete_job,
    enqueue_email,
    fail_job,
    get_job,
)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---- idempotency ----
def test_same_key_twice_gives_one_job(conn):
    a, created_a = enqueue_email(conn, "same", "a@x.com", "s", "b", now=1000.0)
    b, created_b = enqueue_email(conn, "same", "a@x.com", "s", "b", now=1001.0)
    assert (created_a, created_b) == (True, False)
    assert a == b
    assert count(conn, "emails") == 1 and count(conn, "jobs") == 1


def test_different_keys_give_different_jobs(conn):
    a, _ = enqueue_email(conn, "k1", "a@x.com", "s", "b", now=1000.0)
    b, _ = enqueue_email(conn, "k2", "a@x.com", "s", "b", now=1000.0)
    assert a != b and count(conn, "jobs") == 2


# ---- claim ----
def test_claim_marks_running_and_counts_attempt(conn, make_job):
    job_id = make_job()
    job = claim_job(conn, now=1000.0)
    assert job["id"] == job_id
    assert job["status"] == "running" and job["attempts"] == 1
    assert job["locked_until"] == 1060.0


def test_claim_returns_none_when_empty(conn):
    assert claim_job(conn, now=1000.0) is None


def test_two_workers_never_get_same_job(conn, make_job):
    make_job()
    other = connect()  # a second "worker" with its own connection
    first = claim_job(conn, now=1000.0)
    second = claim_job(other, now=1000.0)
    other.close()
    assert first is not None and second is None


def test_threads_never_double_claim(conn, make_job):
    ids = [make_job(f"k{i}") for i in range(20)]
    claimed, lock = [], threading.Lock()

    def worker():
        c = connect()
        try:
            while True:
                job = claim_job(c, now=2000.0)
                if job is None:
                    break
                with lock:
                    claimed.append(job["id"])
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids)  # every job once, none twice


# ---- crash safety ----
def test_expired_lock_is_reclaimed(conn, make_job):
    job_id = make_job()
    claim_job(conn, now=1000.0)  # worker claims, then "dies"
    job = claim_job(conn, now=1061.0)  # lock (60s) has expired
    assert job["id"] == job_id and job["attempts"] == 2


def test_unexpired_lock_is_not_reclaimed(conn, make_job):
    make_job()
    claim_job(conn, now=1000.0)
    assert claim_job(conn, now=1030.0) is None


def test_crash_loop_goes_dead_after_max_attempts(conn, make_job):
    job_id = make_job()
    for t in (1000.0, 1100.0, 1200.0, 1300.0):  # 4 claims, each worker "crashes"
        assert claim_job(conn, now=t) is not None
    assert claim_job(conn, now=1400.0) is None
    job = get_job(conn, job_id)
    assert job["status"] == "dead" and "lock expired" in job["last_error"]


# ---- backoff ----
def test_backoff_grows():
    delays = [backoff_delay(n, rng=lambda: 0) for n in range(1, 6)]
    assert delays == [4.0, 8.0, 16.0, 32.0, 64.0]


def test_backoff_is_capped_at_five_minutes():
    assert backoff_delay(20, rng=lambda: 1) == 300.0


# ---- fail / complete ----
def test_retryable_failure_sets_next_run_at(conn, make_job):
    job_id = make_job()
    claim_job(conn, now=1000.0)
    status = fail_job(conn, job_id, "boom", retryable=True, now=1000.0, rng=lambda: 0)
    job = get_job(conn, job_id)
    assert status == "queued" and job["next_run_at"] == 1004.0
    assert job["last_error"] == "boom" and job["locked_until"] is None
    assert claim_job(conn, now=1003.0) is None  # not due yet
    assert claim_job(conn, now=1004.0) is not None  # due


def test_fatal_failure_goes_dead_immediately(conn, make_job):
    job_id = make_job()
    claim_job(conn, now=1000.0)
    assert fail_job(conn, job_id, "bad key", retryable=False, now=1000.0) == "dead"
    assert get_job(conn, job_id)["attempts"] == 1


def test_dead_after_max_attempts(conn, make_job):
    job_id = make_job()
    outcomes, t = [], 1000.0
    for _ in range(4):
        claim_job(conn, now=t)
        outcomes.append(
            fail_job(conn, job_id, "boom", retryable=True, now=t, rng=lambda: 0)
        )
        t += 1000.0
    assert outcomes == ["queued", "queued", "queued", "dead"]
    assert get_job(conn, job_id)["attempts"] == 4


def test_complete_job(conn, make_job):
    job_id = make_job()
    claim_job(conn, now=1000.0)
    assert complete_job(conn, job_id, now=1001.0) is True
    assert get_job(conn, job_id)["status"] == "succeeded"

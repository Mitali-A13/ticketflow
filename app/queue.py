"""The job queue: enqueue, claim, complete, fail, backoff. All on SQLite."""

import random
import sqlite3
import time
from contextlib import contextmanager

MAX_ATTEMPTS = 4
LOCK_SECONDS = 60
BACKOFF_BASE = 2.0
BACKOFF_CAP = 300.0  # 5 minutes

RESULT_COLUMNS = (
    "category",
    "urgency",
    "customer_name",
    "order_id",
    "summary",
    "suggested_reply",
    "confidence",
    "raw_llm_output",
    "model",
    "latency_ms",
)


@contextmanager
def immediate(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so only one of these
    transactions runs at a time. This is what makes claim() safe."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def backoff_delay(
    attempts: int,
    base: float = BACKOFF_BASE,
    cap: float = BACKOFF_CAP,
    rng=random.random,
) -> float:
    """base * 2^attempts + random jitter, capped. Jitter stops many failed
    jobs from all retrying at the same instant."""
    return min(cap, base * (2**attempts) + rng() * base)


def enqueue_email(
    conn,
    idempotency_key: str,
    sender: str,
    subject: str,
    body: str,
    now: float | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[int, bool]:
    """Store an email and a queued job. Returns (job_id, created).
    Same key again -> (original job_id, False). Never a duplicate."""
    now = _now(now)
    try:
        with immediate(conn):
            row = conn.execute(
                "SELECT j.id FROM emails e JOIN jobs j ON j.email_id = e.id "
                "WHERE e.idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row:
                return row["id"], False
            cur = conn.execute(
                "INSERT INTO emails (idempotency_key, sender, subject, body, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (idempotency_key, sender, subject, body, now),
            )
            email_id = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO jobs (email_id, status, attempts, max_attempts, "
                "next_run_at, created_at, updated_at) "
                "VALUES (?, 'queued', 0, ?, ?, ?, ?)",
                (email_id, max_attempts, now, now, now),
            )
            return cur.lastrowid, True
    except sqlite3.IntegrityError:
        # Safety net: the UNIQUE index caught a race we somehow missed.
        row = conn.execute(
            "SELECT j.id FROM emails e JOIN jobs j ON j.email_id = e.id "
            "WHERE e.idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row["id"], False


def get_job(conn, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_email(conn, email_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    return dict(row) if row else None


def get_result(conn, job_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM results WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def claim_job(
    conn, now: float | None = None, lock_seconds: int = LOCK_SECONDS
) -> dict | None:
    """Atomically claim one job, or return None if nothing is ready.
    Ready = queued and due, OR running with an expired lock (worker crashed)."""
    now = _now(now)
    with immediate(conn):
        # A job whose lock expired too many times is a crash loop: dead-letter it.
        conn.execute(
            "UPDATE jobs SET status='dead', locked_until=NULL, updated_at=?, "
            "last_error='lock expired after max attempts (worker crashed?)' "
            "WHERE status='running' AND locked_until < ? AND attempts >= max_attempts",
            (now, now),
        )
        row = conn.execute(
            "SELECT id FROM jobs "
            "WHERE (status='queued' AND next_run_at <= ?) "
            "   OR (status='running' AND locked_until < ?) "
            "ORDER BY next_run_at, id LIMIT 1",
            (now, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status='running', attempts=attempts+1, "
            "locked_until=?, updated_at=? WHERE id=?",
            (now + lock_seconds, now, row["id"]),
        )
        return get_job(conn, row["id"])


def complete_job(conn, job_id: int, now: float | None = None) -> bool:
    now = _now(now)
    with immediate(conn):
        cur = conn.execute(
            "UPDATE jobs SET status='succeeded', locked_until=NULL, "
            "last_error=NULL, updated_at=? WHERE id=? AND status='running'",
            (now, job_id),
        )
        return cur.rowcount == 1


def complete_with_result(
    conn, job_id: int, status: str, result: dict, now: float | None = None
) -> bool:
    """Finish a job AND save its result in ONE transaction, so a job is never
    'succeeded' without a result row. status: 'succeeded' or 'needs_review'.
    Returns False (and saves nothing) if the job is no longer running."""
    if status not in ("succeeded", "needs_review"):
        raise ValueError(f"bad final status: {status}")
    now = _now(now)
    with immediate(conn):
        cur = conn.execute(
            "UPDATE jobs SET status=?, locked_until=NULL, last_error=NULL, "
            "updated_at=? WHERE id=? AND status='running'",
            (status, now, job_id),
        )
        if cur.rowcount != 1:
            return False
        cols = ", ".join(RESULT_COLUMNS)
        marks = ", ".join("?" for _ in RESULT_COLUMNS)
        conn.execute(
            f"INSERT INTO results (job_id, {cols}) VALUES (?, {marks})",
            (job_id, *[result[c] for c in RESULT_COLUMNS]),
        )
        return True


def fail_job(
    conn,
    job_id: int,
    error: str,
    retryable: bool,
    now: float | None = None,
    rng=random.random,
) -> str | None:
    """Record a failure. Returns the new status ('queued' or 'dead'),
    or None if the job was not running."""
    now = _now(now)
    with immediate(conn):
        job = conn.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE id=? AND status='running'",
            (job_id,),
        ).fetchone()
        if job is None:
            return None
        if (not retryable) or job["attempts"] >= job["max_attempts"]:
            conn.execute(
                "UPDATE jobs SET status='dead', locked_until=NULL, last_error=?, "
                "updated_at=? WHERE id=?",
                (error, now, job_id),
            )
            return "dead"
        delay = backoff_delay(job["attempts"], rng=rng)
        conn.execute(
            "UPDATE jobs SET status='queued', locked_until=NULL, last_error=?, "
            "next_run_at=?, updated_at=? WHERE id=?",
            (error, now + delay, now, job_id),
        )
        return "queued"

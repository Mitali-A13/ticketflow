"""Operational queries: list jobs, manual retry, metrics."""

import time

from app.queue import immediate

STATUSES = ("queued", "running", "succeeded", "needs_review", "dead")


def list_jobs(conn, status: str | None = None, limit: int = 50) -> list[dict]:
    if status is None:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        )
    return [dict(r) for r in rows.fetchall()]


def retry_job(conn, job_id: int, now: float | None = None) -> str:
    """Move a dead job back to queued with a fresh set of attempts.
    Returns 'queued', 'not_dead' or 'not_found'. last_error is kept for debugging."""
    now = time.time() if now is None else now
    with immediate(conn):
        cur = conn.execute(
            "UPDATE jobs SET status='queued', attempts=0, next_run_at=?, "
            "locked_until=NULL, updated_at=? WHERE id=? AND status='dead'",
            (now, now, job_id),
        )
        if cur.rowcount == 1:
            return "queued"
        exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        return "not_dead" if exists else "not_found"


def get_metrics(conn) -> dict:
    counts = {s: 0 for s in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
        counts[row["status"]] = row["n"]
    total = sum(counts.values())
    avg = conn.execute(
        "SELECT AVG(attempts) AS a FROM jobs WHERE attempts > 0"
    ).fetchone()["a"]
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs j JOIN results r ON r.job_id = j.id "
        "LEFT JOIN reviews v ON v.result_id = r.id "
        "WHERE j.status = 'needs_review' AND v.id IS NULL"
    ).fetchone()["n"]
    return {
        "counts": counts,
        "total": total,
        "queue_depth": counts["queued"],
        "pending_review": pending,
        "avg_attempts": None if avg is None else round(avg, 3),
    }

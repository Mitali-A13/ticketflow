"""Human review: the pending queue and saving a review."""

import json
import time

from app.extract import CONFIDENCE_THRESHOLD
from app.queue import immediate

EDITABLE = (
    "category",
    "urgency",
    "customer_name",
    "order_id",
    "summary",
    "suggested_reply",
)


def likely_reasons(row: dict) -> list[str]:
    """Recomputed from the stored result (reasons themselves are not stored)."""
    reasons = []
    if row["confidence"] is not None and row["confidence"] < CONFIDENCE_THRESHOLD:
        reasons.append(f"low confidence ({row['confidence']:.2f})")
    if row["category"] == "other":
        reasons.append("category is other")
    if row["urgency"] == "high":
        reasons.append("urgency is high")
    if not row["order_id"]:
        reasons.append(
            "no order id (missing, or removed because it was not in the email)"
        )
    return reasons


def pending_reviews(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT r.id AS result_id, r.job_id, r.category, r.urgency, r.customer_name, "
        "r.order_id, r.summary, r.suggested_reply, r.confidence, "
        "e.sender, e.subject, e.body "
        "FROM results r JOIN jobs j ON j.id = r.job_id JOIN emails e ON e.id = j.email_id "
        "LEFT JOIN reviews v ON v.result_id = r.id "
        "WHERE j.status = 'needs_review' AND v.id IS NULL "
        "ORDER BY r.id LIMIT ?",
        (limit,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["reasons"] = likely_reasons(item)
        items.append(item)
    return items


def get_result_by_id(conn, result_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM results WHERE id = ?", (result_id,)).fetchone()
    return dict(row) if row else None


def save_review(
    conn, result_id: int, action: str, corrected: dict | None, now: float | None = None
) -> str:
    """Returns 'ok', 'not_found', 'not_reviewable' or 'already_reviewed'.
    The original result row is never changed, so the model's answer is kept."""
    now = time.time() if now is None else now
    with immediate(conn):
        row = conn.execute(
            "SELECT j.status FROM results r JOIN jobs j ON j.id = r.job_id WHERE r.id = ?",
            (result_id,),
        ).fetchone()
        if row is None:
            return "not_found"
        if row["status"] != "needs_review":
            return "not_reviewable"
        if conn.execute(
            "SELECT 1 FROM reviews WHERE result_id = ?", (result_id,)
        ).fetchone():
            return "already_reviewed"
        conn.execute(
            "INSERT INTO reviews (result_id, reviewer_action, corrected_fields, reviewed_at) "
            "VALUES (?, ?, ?, ?)",
            (result_id, action, json.dumps(corrected) if corrected else None, now),
        )
        return "ok"

"""Worker: claim a job, process it, record the outcome.
Run with:  python -m app.worker
FAKE_LLM=true python -m app.worker   (no API key needed)
"""

import logging
import random
import sys
import threading
import time

from dotenv import load_dotenv

from app.db import connect, init_db
from app.errors import FatalError, RetryableError
from app.extract import make_processor
from app.llm import get_llm
from app.logs import setup_logging
from app.queue import claim_job, complete_job, complete_with_result, fail_job, get_email

log = logging.getLogger("worker")


def fake_processor(job: dict, email: dict) -> None:
    """Day 1 stand-in. Behaviour is controlled by the email body, so we can
    test every queue path without any AI. Kept so the Day 1 tests still run."""
    body = email["body"]
    if "FAKE_FATAL" in body:
        raise FatalError("fake fatal error")
    if "FAKE_RETRY_ALWAYS" in body:
        raise RetryableError("fake retryable error")
    if "FAKE_RETRY_ONCE" in body and job["attempts"] < 2:
        raise RetryableError("fake retryable error (first attempt)")


def _fail(conn, job, message, retryable, now, rng):
    status = fail_job(conn, job["id"], message, retryable=retryable, now=now, rng=rng)
    log.warning(
        "job failed",
        extra={
            "job_id": job["id"],
            "error": message,
            "attempts": job["attempts"],
            "new_status": status,
        },
    )
    return status


def process_one(
    conn, processor=fake_processor, now: float | None = None, rng=random.random
) -> str | None:
    """Handle at most one job. Returns the job's new status, or None if idle.
    A processor returns None (plain success) or an Outcome (status + result)."""
    job = claim_job(conn, now=now)
    if job is None:
        return None
    email = get_email(conn, job["email_id"])
    try:
        outcome = processor(job, email)
    except FatalError as e:
        return _fail(conn, job, f"fatal: {e}", False, now, rng)
    except RetryableError as e:
        return _fail(conn, job, f"retryable: {e}", True, now, rng)
    except Exception as e:  # unknown bug: retry, the attempt cap protects us
        return _fail(conn, job, f"unexpected {type(e).__name__}: {e}", True, now, rng)

    if outcome is None:
        complete_job(conn, job["id"], now=now)
        status = "succeeded"
    else:
        saved = complete_with_result(
            conn, job["id"], outcome.status, outcome.result, now=now
        )
        status = outcome.status if saved else "lock_lost"
    log.info(
        "job finished",
        extra={"job_id": job["id"], "attempts": job["attempts"], "new_status": status},
    )
    return status


def run_forever(
    poll_interval: float = 1.0, stop: threading.Event | None = None
) -> int | None:
    """Run the worker loop. Returns 1 if it cannot start. Pass an Event to stop it."""
    load_dotenv()  # reads .env; real environment variables win over .env
    setup_logging()
    try:
        llm = get_llm()
    except FatalError as e:
        log.error("cannot start: %s", e)
        return 1
    processor = make_processor(llm)
    conn = connect()
    init_db(conn)
    log.info("worker started", extra={"llm": getattr(llm, "model", "?")})

    def pause() -> None:
        if stop is not None:
            stop.wait(poll_interval)
        else:
            time.sleep(poll_interval)

    try:
        while stop is None or not stop.is_set():
            try:
                outcome = process_one(conn, processor=processor)
            except Exception:
                # e.g. a database error. The job's lock will expire and it will
                # be reclaimed (the crash-safety path from Day 1).
                log.exception("worker loop error")
                pause()
                continue
            if outcome is None:
                pause()  # idle: wait, then poll again
    except KeyboardInterrupt:
        log.info("worker stopped")
    finally:
        conn.close()
    return None


if __name__ == "__main__":
    sys.exit(run_forever() or 0)

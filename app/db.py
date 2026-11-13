"""SQLite connection and schema."""

import os
import sqlite3


def db_path() -> str:
    # Read at call time so tests can point this at a temp file.
    return os.environ.get("TICKETFLOW_DB", "ticketflow.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(
        path or db_path(),
        timeout=5.0,
        isolation_level=None,  # autocommit: we write BEGIN/COMMIT ourselves
        check_same_thread=False,  # FastAPI may use a connection from another thread
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s if another writer is busy
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    sender          TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    received_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id     INTEGER NOT NULL REFERENCES emails(id),
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','succeeded','needs_review','dead')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 4,
    next_run_at  REAL NOT NULL,
    locked_until REAL,
    last_error   TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER NOT NULL REFERENCES jobs(id),
    category         TEXT,
    urgency          TEXT,
    customer_name    TEXT,
    order_id         TEXT,
    summary          TEXT,
    suggested_reply  TEXT,
    confidence       REAL,
    raw_llm_output   TEXT,
    model            TEXT,
    latency_ms       INTEGER
);

CREATE TABLE IF NOT EXISTS reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id        INTEGER NOT NULL REFERENCES results(id),
    reviewer_action  TEXT NOT NULL CHECK (reviewer_action IN ('approved','corrected')),
    corrected_fields TEXT,
    reviewed_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_next ON jobs(status, next_run_at);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

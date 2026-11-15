"""Settings for running as one public demo container (API and worker together)."""

import os
import threading
import time

from app.demo import SAMPLES
from app.queue import enqueue_email
from app.worker import run_forever


def demo_key_hint() -> str | None:
    """A key to show on the login page. Only set this for a public demo."""
    return os.environ.get("DEMO_KEY_HINT", "").strip() or None


def seed_demo_data(conn) -> int:
    """Queue the sample emails. Safe to call repeatedly: the keys make it idempotent."""
    now = time.time()
    created = 0
    for i, sample in enumerate(SAMPLES, 1):
        _, is_new = enqueue_email(
            conn,
            f"seed-{i}",
            sample["sender"],
            sample["subject"],
            sample["body"],
            now=now,
        )
        created += is_new
    return created


def start_embedded_worker() -> threading.Event:
    """Run the worker loop in a background thread. Set the returned event to stop it."""
    stop = threading.Event()
    threading.Thread(
        target=run_forever, kwargs={"stop": stop}, name="embedded-worker", daemon=True
    ).start()
    return stop

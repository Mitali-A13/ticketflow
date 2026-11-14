"""Load test: enqueue many emails through the API, then wait for the worker.

Start the stack with a fake LLM and a high rate limit first:
  FAKE_LLM=true RATE_LIMIT_PER_MIN=100000 docker compose up --build -d
Then, from the project root:
  python scripts/load_test.py --requests 200 --concurrency 20
Reads TICKETFLOW_API_KEY from your environment or .env.
"""

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile of a list of numbers."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def finished(metrics: dict) -> int:
    c = metrics["counts"]
    return c["succeeded"] + c["needs_review"] + c["dead"]


async def fetch_metrics(client: httpx.AsyncClient) -> dict:
    r = await client.get("/metrics")
    if r.status_code == 429:
        raise SystemExit(
            "Rate limited (429). Start the stack with RATE_LIMIT_PER_MIN=100000."
        )
    if r.status_code == 401:
        raise SystemExit("401: check TICKETFLOW_API_KEY.")
    r.raise_for_status()
    return r.json()


async def run(args) -> int:
    key = os.environ.get("TICKETFLOW_API_KEY", "").strip()
    if not key:
        print("TICKETFLOW_API_KEY is not set (put it in .env).")
        return 1
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(
        base_url=args.url, headers={"X-API-Key": key}, timeout=30, limits=limits
    ) as client:
        try:
            before = await fetch_metrics(client)
        except httpx.ConnectError:
            print(f"Cannot reach {args.url}. Is the stack running?")
            return 1
        done_before = finished(before)

        run_id = int(time.time())
        sem = asyncio.Semaphore(args.concurrency)
        results: list[tuple] = []  # (status code or error name, latency in ms)

        async def one(i: int) -> None:
            payload = {
                "sender": f"load{i}@example.com",
                "subject": f"Load test {i}",
                "body": f"Hi, where is my order ORD-{10000 + i}? It is late. Thanks",
            }
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.post(
                        "/emails",
                        json=payload,
                        headers={"Idempotency-Key": f"load-{run_id}-{i}"},
                    )
                    code = r.status_code
                except httpx.HTTPError as e:
                    code = type(e).__name__
                results.append((code, (time.perf_counter() - t0) * 1000))

        start = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(args.requests)))
        enqueue_s = time.perf_counter() - start

        accepted = [lat for code, lat in results if code == 202]
        others: dict = {}
        for code, _ in results:
            if code != 202:
                others[str(code)] = others.get(str(code), 0) + 1
        if not accepted:
            print("No request was accepted:", others)
            return 1

        timed_out = False
        deadline = time.perf_counter() + args.timeout
        while True:
            m = await fetch_metrics(client)
            if finished(m) - done_before >= len(accepted):
                break
            if time.perf_counter() > deadline:
                timed_out = True
                break
            await asyncio.sleep(0.25)
        total_s = time.perf_counter() - start
        after = await fetch_metrics(client)

    states = {
        s: after["counts"][s] - before["counts"][s]
        for s in ("succeeded", "needs_review", "dead")
    }
    summary = {
        "when_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": platform.platform(),
        "python": platform.python_version(),
        "target": args.url,
        "requests_sent": args.requests,
        "concurrency": args.concurrency,
        "accepted_202": len(accepted),
        "other_results": others,
        "enqueue_seconds": round(enqueue_s, 3),
        "enqueue_requests_per_s": round(len(accepted) / enqueue_s, 1),
        "enqueue_latency_ms": {
            "min": round(min(accepted), 1),
            "median": round(statistics.median(accepted), 1),
            "p95": round(percentile(accepted, 95), 1),
            "max": round(max(accepted), 1),
        },
        "drain_completed": not timed_out,
        "total_seconds": round(total_s, 3),
        "jobs_per_s_end_to_end": None
        if timed_out
        else round(len(accepted) / total_s, 1),
        "final_states": states,
    }
    print(json.dumps(summary, indent=2))
    out = ROOT / "docs" / "load_test_last.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")
    if timed_out:
        print(
            "WARNING: the worker did not finish all jobs before the timeout. "
            "Do not quote a jobs/s number from this run."
        )
        return 1
    if others:
        print("WARNING: some requests were not accepted (see other_results).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--url", default=os.environ.get("TICKETFLOW_URL", "http://localhost:8000")
    )
    p.add_argument("--requests", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument(
        "--timeout", type=float, default=120.0, help="seconds to wait for the worker"
    )
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

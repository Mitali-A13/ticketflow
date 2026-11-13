"""Run the evaluation through the REAL pipeline (queue, worker logic, retries).

  python -m eval.run_eval --split dev --label v1
  FAKE_LLM=true python -m eval.run_eval --split dev     (dry run, plumbing only)
  python -m eval.run_eval --split test                  (only after freezing, see PLAN.md)

Each run uses a fresh database and writes eval/runs/<split>-<label>-<time>.json.
"""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.db import connect, init_db
from app.errors import FatalError, MalformedOutputError, RetryableError
from app.extract import (
    CONFIDENCE_THRESHOLD,
    SYSTEM_PROMPT,
    make_processor,
    parse_json_object,
)
from app.llm import env_true, get_llm
from app.logs import setup_logging
from app.models import Extraction
from app.queue import enqueue_email, get_job, get_result
from app.worker import process_one
from eval.baseline import classify
from eval.scoring import (
    FIELDS,
    check_criteria,
    compute_metrics,
    email_text,
    field_matches,
    load_emails,
)

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "eval" / "PLAN.md"
RUNS = ROOT / "eval" / "runs"


# ------------------------------------------------------------ test-set guard
def frozen_settings_filled(text: str) -> bool:
    """True if the 'Frozen settings' section of PLAN.md has no ____ blanks left."""
    marker = "## Frozen settings"
    if marker not in text:
        return False
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    return "____" not in section


def git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=ROOT, check=True
        )
        return r.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def final_run_problems(rerun_reason: str | None, fake: bool) -> list[str]:
    problems = []
    if fake:
        problems.append("FAKE_LLM is on: the final evaluation must use the real model")
    if not frozen_settings_filled(PLAN.read_text(encoding="utf-8")):
        problems.append(
            "fill in 'Frozen settings' in eval/PLAN.md (no ____ left) and commit it"
        )
    if git("status", "--porcelain", "--untracked-files=no"):
        problems.append(
            "tracked files have uncommitted changes (or git is unavailable): "
            "commit the frozen prompt, threshold and PLAN.md first"
        )
    if list(RUNS.glob("test-*.json")) and not rerun_reason:
        problems.append(
            "a test run already exists; PLAN.md allows a rerun only for "
            'infrastructure failures (use --rerun-reason "...")'
        )
    return problems


# ------------------------------------------------------------ running
def make_recording_processor(llm, attempt_log: dict):
    """Wraps the real processor and records how every attempt ended."""
    inner = make_processor(llm)

    def processor(job, email):
        entry = {"attempt": job["attempts"], "kind": "valid", "error": None}
        attempt_log.setdefault(job["id"], []).append(entry)
        try:
            return inner(job, email)
        except MalformedOutputError as e:  # must come before RetryableError
            entry.update(kind="malformed", error=str(e)[:200])
            raise
        except FatalError as e:
            entry.update(kind="fatal", error=str(e)[:200])
            raise
        except RetryableError as e:
            entry.update(kind="retryable", error=str(e)[:200])
            raise
        except Exception as e:
            entry.update(kind="unexpected", error=f"{type(e).__name__}: {e}"[:200])
            raise

    return processor


def build_record(conn, email: dict, job_id: int, attempt_log: dict) -> dict:
    job = get_job(conn, job_id)
    result = get_result(conn, job_id)
    final = raw = None
    raw_text = ""
    if result:
        final = {f: result[f] for f in FIELDS}
        raw_text = result["raw_llm_output"] or ""
        try:  # same cleaning rules as the pipeline, but before the order-id check
            raw = Extraction.model_validate(parse_json_object(raw_text)).model_dump()
        except Exception:
            raw = None
    markers = email.get("injection_markers", [])
    hit = None
    if markers and result:
        hit = any(m.casefold() in raw_text.casefold() for m in markers)
    return {
        "id": email["id"],
        "split": email["split"],
        "tags": email["tags"],
        "text": email_text(email),
        "gold": email["gold"],
        "status": job["status"],
        "attempts": job["attempts"],
        "last_error": job["last_error"],
        "final": final,
        "raw": raw,
        "raw_output": raw_text or None,
        "summary": result["summary"] if result else None,
        "suggested_reply": result["suggested_reply"] if result else None,
        "confidence": result["confidence"] if result else None,
        "latency_ms": result["latency_ms"] if result else None,
        "baseline_category": classify(email["subject"], email["body"]),
        "injection_markers": markers,
        "injection_hit": hit,
        "attempt_log": attempt_log.get(job_id, []),
    }


# ------------------------------------------------------------ report
def pct(d: dict) -> str:
    if not d["n"]:
        return f"{d['k']}/0"
    return f"{d['k']}/{d['n']} ({100 * d['k'] / d['n']:.1f}%)"


def print_report(meta, m, criteria, records) -> None:
    print()
    print(
        f"split={meta['split']}  model={meta['model']}  threshold={meta['confidence_threshold']}"
        f"  commit={meta['git_commit'][:8]}  fake={meta['fake_llm']}"
    )
    print(f"emails: {m['n']}   with a valid result: {m['n_with_result']}")
    print()
    print("Accuracy (exact match, no result counts as wrong)")
    for field in FIELDS:
        print(f"  {field:<14} {pct(m[field])}")
    print(f"  keyword baseline (category): {pct(m['baseline_category'])}")
    h = m["hallucination"]
    print(
        f"Hallucination on raw output: order_id {pct(h['order_id'])}, "
        f"customer_name {pct(h['customer_name'])}"
    )
    print(
        f"Auto-accepted: {pct(m['auto_accept'])}; "
        f"category accuracy of auto-accepted: {pct(m['auto_accept_category'])}"
    )
    print(f"First attempt: {m['first_attempt']}")
    print(
        f"Median LLM latency (ms): {m['median_latency_ms']}   attempts per job: {m['attempts']}"
    )
    print(f"Injection markers: {m['injection']}")
    if criteria:
        print()
        print("Success criteria (frozen in PLAN.md)")
        for key, c in criteria.items():
            verdict = {True: "PASS", False: "FAIL", None: "INCONCLUSIVE"}[c["pass"]]
            print(f"  {key}: {verdict}  {c['detail']}")
    print()
    print("Mismatches")
    count = 0
    for r in records:
        if r["final"] is None:
            print(
                f"  {r['id']} {r['tags']}: NO RESULT, status={r['status']}, error={r['last_error']}"
            )
            count += 1
            continue
        for field in FIELDS:
            if not field_matches(field, r["gold"][field], r["final"][field]):
                print(
                    f"  {r['id']} {r['tags']} {field}: gold={r['gold'][field]!r} "
                    f"got={r['final'][field]!r} ({r['status']}, conf {r['confidence']})"
                )
                count += 1
        if r["baseline_category"] != r["gold"]["category"]:
            print(
                f"  {r['id']} {r['tags']} BASELINE category: gold={r['gold']['category']} "
                f"got={r['baseline_category']}"
            )
    if count == 0:
        print("  (no LLM mismatches)")


# ------------------------------------------------------------ main
def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--split", choices=["dev", "test"], required=True)
    p.add_argument("--label", default="run")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds")
    p.add_argument(
        "--delay",
        type=float,
        default=None,
        help="seconds to wait after each attempt (default 2.0, 0 for fake)",
    )
    p.add_argument("--rerun-reason", default=None)
    args = p.parse_args(argv)

    load_dotenv()
    setup_logging(logging.WARNING)
    fake = env_true("FAKE_LLM")
    RUNS.mkdir(parents=True, exist_ok=True)

    if args.split == "test":
        problems = final_run_problems(args.rerun_reason, fake)
        if problems:
            print("Refusing to run the TEST set:")
            for problem in problems:
                print(" -", problem)
            return 2
    try:
        llm = get_llm()
    except FatalError as e:
        print(f"cannot start: {e}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{args.split}-{'fake' if fake else args.label}-{stamp}"
    conn = connect(str(RUNS / f"{stem}.db"))
    init_db(conn)

    emails = load_emails(split=args.split)
    jobs = {}
    for e in emails:
        job_id, _ = enqueue_email(
            conn,
            f"eval-{e['id']}",
            e["sender"],
            e["subject"],
            e["body"],
            now=time.time(),
        )
        jobs[e["id"]] = job_id
    print(
        f"{len(emails)} emails queued ({args.split}); processing with {getattr(llm, 'model', '?')}"
    )

    delay = args.delay if args.delay is not None else (0.0 if fake else 2.0)
    attempt_log: dict = {}
    processor = make_recording_processor(llm, attempt_log)
    started = time.time()
    attempts_done = 0
    timed_out = False
    while True:
        if time.time() - started > args.timeout:
            timed_out = True
            print("TIMEOUT: some jobs did not finish; they count as no result")
            break
        outcome = process_one(conn, processor=processor)
        if outcome is None:
            pending = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if pending == 0:
                break
            time.sleep(0.5)  # jobs waiting out a backoff
            continue
        attempts_done += 1
        print(f"  attempt {attempts_done}: {outcome}", flush=True)
        if delay:
            time.sleep(delay)

    records = [build_record(conn, e, jobs[e["id"]], attempt_log) for e in emails]
    conn.close()
    metrics = compute_metrics(records)
    criteria = check_criteria(metrics) if args.split == "test" else None

    meta = {
        "split": args.split,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": getattr(llm, "model", "?"),
        "fake_llm": fake,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "reasoning_effort": getattr(llm, "extra_body", {}).get("reasoning_effort"),
        "temperature": 0,
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16],
        "git_commit": git("rev-parse", "HEAD"),
        "git_tracked_changes": bool(
            git("status", "--porcelain", "--untracked-files=no")
        ),
        "delay_s": delay,
        "rerun_reason": args.rerun_reason,
        "timed_out": timed_out,
        "wall_seconds": round(time.time() - started, 1),
    }
    out_path = RUNS / f"{stem}.json"
    out_path.write_text(
        json.dumps(
            {
                "meta": meta,
                "metrics": metrics,
                "criteria": criteria,
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print_report(meta, metrics, criteria, records)
    print(f"\nSaved {out_path.relative_to(ROOT)}")
    if args.split == "test":
        print("Now write eval/RESULTS.md from this output and commit the JSON file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

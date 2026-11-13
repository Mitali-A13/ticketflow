"""Pure scoring functions for the evaluation. No network, no database.
Everything is reported as k/n so the denominators are always visible."""

import json
import statistics
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "emails.jsonl"
FIELDS = ("category", "urgency", "customer_name", "order_id")


def load_emails(path: Path = DATA_PATH, split: str | None = None) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                if split is None or row["split"] == split:
                    rows.append(row)
    return rows


def email_text(email: dict) -> str:
    """The same text the pipeline checks order ids against."""
    return f"{email['subject']}\n{email['body']}"


def norm_name(v):
    return None if v is None else str(v).strip().casefold()


def norm_id(v):
    return None if v is None else str(v).strip()


def field_matches(field: str, gold, got) -> bool:
    """Exact match. Names ignore case and outer spaces; null equals null."""
    if field == "customer_name":
        return norm_name(gold) == norm_name(got)
    if field == "order_id":
        return norm_id(gold) == norm_id(got)
    return gold == got


def _frac(k: int, n: int) -> dict:
    return {"k": k, "n": n}


def compute_metrics(records: list[dict]) -> dict:
    """Each record is one email's outcome (see run_eval.build_record).
    A job with no result (dead, unfinished) counts as wrong for accuracy."""
    n = len(records)

    def final_matches(r, field):
        f = r["final"]
        return f is not None and field_matches(field, r["gold"][field], f.get(field))

    m = {"n": n, "n_with_result": sum(r["final"] is not None for r in records)}
    for field in FIELDS:
        m[field] = _frac(sum(final_matches(r, field) for r in records), n)

    # Hallucination: measured on the RAW model output (before the code's
    # literal-match check nulls an invented order id). Denominator = emails
    # with a parseable result.
    raws = [r for r in records if r["raw"] is not None]
    bad_ids = sum(
        1
        for r in raws
        if r["raw"]["order_id"] is not None
        and norm_id(r["raw"]["order_id"]) not in r["text"]
    )
    bad_names = sum(
        1
        for r in raws
        if r["raw"]["customer_name"] is not None
        and norm_name(r["raw"]["customer_name"]) not in r["text"].casefold()
    )
    m["hallucination"] = {
        "order_id": _frac(bad_ids, len(raws)),
        "customer_name": _frac(bad_names, len(raws)),
    }

    auto = [r for r in records if r["status"] == "succeeded"]
    m["auto_accept"] = _frac(len(auto), n)
    m["auto_accept_category"] = _frac(
        sum(final_matches(r, "category") for r in auto), len(auto)
    )

    first = {
        "valid": 0,
        "malformed": 0,
        "retryable": 0,
        "fatal": 0,
        "unexpected": 0,
        "not_run": 0,
    }
    for r in records:
        log = r["attempt_log"]
        kind = log[0]["kind"] if log else "not_run"
        first[kind if kind in first else "unexpected"] += 1
    m["first_attempt"] = first

    latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None]
    m["median_latency_ms"] = statistics.median(latencies) if latencies else None
    attempts = [r["attempts"] for r in records]
    m["attempts"] = {
        "mean": round(sum(attempts) / n, 2) if n else None,
        "max": max(attempts) if attempts else None,
    }

    with_markers = [r for r in records if r["injection_markers"]]
    m["injection"] = {
        "with_markers": len(with_markers),
        "marker_in_output": sum(r["injection_hit"] is True for r in with_markers),
        "no_output": sum(r["injection_hit"] is None for r in with_markers),
    }
    m["baseline_category"] = _frac(
        sum(r["baseline_category"] == r["gold"]["category"] for r in records), n
    )
    return m


def check_criteria(m: dict) -> dict:
    """The frozen success criteria from eval/PLAN.md. TEST split only.
    Integer arithmetic on purpose, so boundaries (26/30, +3 emails) are exact."""
    n = m["n"]
    llm = m["category"]["k"]
    base = m["baseline_category"]["k"]
    auto_k, auto_n = m["auto_accept_category"]["k"], m["auto_accept_category"]["n"]
    out = {
        "A": {
            "pass": llm * 100 >= 85 * n,
            "detail": f"LLM category accuracy {llm}/{n}, need at least 85%",
        },
        "B": {
            "pass": (llm - base) * 10 >= n,
            "detail": f"LLM {llm}/{n} vs baseline {base}/{n}, need +10 points",
        },
        "D": {
            "pass": m["n_with_result"] >= n - 1,
            "detail": f"{m['n_with_result']}/{n} emails ended with a valid result, need at least {n - 1}",
        },
    }
    if auto_n < 10:
        out["C"] = {
            "pass": None,
            "detail": f"only {auto_n} auto-accepted emails: inconclusive, not a pass",
        }
    else:
        out["C"] = {
            "pass": auto_k * n > llm * auto_n,
            "detail": f"auto-accepted {auto_k}/{auto_n} vs all {llm}/{n}, need strictly higher",
        }
    return dict(sorted(out.items()))

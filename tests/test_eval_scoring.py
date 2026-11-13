from eval.scoring import check_criteria, compute_metrics, field_matches, load_emails


def gold(**over):
    base = {
        "category": "shipping",
        "urgency": "medium",
        "customer_name": "Asha",
        "order_id": "ORD-1",
    }
    base.update(over)
    return base


def rec(**over):
    base = {
        "id": "e1",
        "split": "test",
        "tags": [],
        "text": "Order ORD-1 is late. Asha",
        "gold": gold(),
        "status": "succeeded",
        "attempts": 1,
        "final": gold(),
        "raw": gold(),
        "confidence": 0.9,
        "latency_ms": 100,
        "baseline_category": "shipping",
        "injection_markers": [],
        "injection_hit": None,
        "attempt_log": [{"attempt": 1, "kind": "valid", "error": None}],
    }
    base.update(over)
    return base


def no_result(**over):
    return rec(status="dead", attempts=4, final=None, raw=None, latency_ms=None, **over)


def fake_metrics(n=30, llm=27, base=20, auto_k=12, auto_n=12, with_result=30):
    return {
        "n": n,
        "n_with_result": with_result,
        "category": {"k": llm, "n": n},
        "baseline_category": {"k": base, "n": n},
        "auto_accept_category": {"k": auto_k, "n": auto_n},
    }


# ---- matching ----
def test_name_matching_ignores_case_and_outer_spaces():
    assert field_matches("customer_name", "Asha Rao", "  asha rao ")
    assert not field_matches("customer_name", "Asha Rao", "Asha")


def test_order_id_matching_is_exact_but_trimmed():
    assert field_matches("order_id", "#88213", " #88213 ")
    assert not field_matches("order_id", "#88213", "88213")
    assert not field_matches("order_id", "ORD-1", "ord-1")


# ---- metrics ----
def test_dead_job_counts_as_wrong():
    m = compute_metrics([rec(), no_result()])
    assert m["category"] == {"k": 1, "n": 2}
    assert m["n_with_result"] == 1


def test_null_equals_null():
    r = rec(
        gold=gold(order_id=None), final=gold(order_id=None), raw=gold(order_id=None)
    )
    assert compute_metrics([r])["order_id"] == {"k": 1, "n": 1}


def test_hallucination_uses_raw_output_not_final():
    invented = rec(
        text="Where is my parcel? Asha",
        raw=gold(order_id="ORD-9"),
        final=gold(order_id=None),
    )
    m = compute_metrics([invented, rec()])
    assert m["hallucination"]["order_id"] == {"k": 1, "n": 2}
    assert m["hallucination"]["customer_name"] == {"k": 0, "n": 2}
    assert m["order_id"]["k"] == 1  # the nulled id is still wrong against the gold


def test_name_hallucination_ignores_case():
    ok = rec(text="regards asha", raw=gold(order_id=None, customer_name="Asha"))
    bad = rec(text="regards asha", raw=gold(order_id=None, customer_name="Zed"))
    m = compute_metrics([ok, bad])
    assert m["hallucination"]["customer_name"] == {"k": 1, "n": 2}
    assert m["hallucination"]["order_id"] == {"k": 0, "n": 2}


def test_auto_accept_numbers():
    a = rec()
    b = rec(final=gold(category="billing"))
    c = rec(status="needs_review")
    d = rec(status="needs_review", final=gold(category="billing"))
    m = compute_metrics([a, b, c, d])
    assert m["auto_accept"] == {"k": 2, "n": 4}
    assert m["auto_accept_category"] == {"k": 1, "n": 2}
    assert m["category"] == {"k": 2, "n": 4}


def test_first_attempt_counts():
    retried = rec(
        attempts=2,
        attempt_log=[
            {"attempt": 1, "kind": "malformed", "error": "x"},
            {"attempt": 2, "kind": "valid", "error": None},
        ],
    )
    never_ran = rec(
        status="queued",
        final=None,
        raw=None,
        latency_ms=None,
        attempts=0,
        attempt_log=[],
    )
    m = compute_metrics([rec(), retried, never_ran])
    assert m["first_attempt"] == {
        "valid": 1,
        "malformed": 1,
        "retryable": 0,
        "fatal": 0,
        "unexpected": 0,
        "not_run": 1,
    }


def test_median_latency_and_attempts():
    m = compute_metrics(
        [rec(latency_ms=100, attempts=1), rec(latency_ms=300, attempts=2), no_result()]
    )
    assert m["median_latency_ms"] == 200.0
    assert m["attempts"] == {"mean": 2.33, "max": 4}


def test_injection_marker_counts():
    m = compute_metrics(
        [
            rec(injection_markers=["APPROVED"], injection_hit=True),
            rec(injection_markers=["X"], injection_hit=False),
            no_result(injection_markers=["X"], injection_hit=None),
            rec(),
        ]
    )
    assert m["injection"] == {"with_markers": 3, "marker_in_output": 1, "no_output": 1}


def test_baseline_counts():
    m = compute_metrics([rec(), rec(baseline_category="other")])
    assert m["baseline_category"] == {"k": 1, "n": 2}


# ---- success criteria ----
def test_criteria_all_pass():
    c = check_criteria(fake_metrics())
    assert all(v["pass"] is True for v in c.values())


def test_criterion_a_boundary():
    assert check_criteria(fake_metrics(llm=26))["A"]["pass"] is True  # 86.7%
    assert check_criteria(fake_metrics(llm=25))["A"]["pass"] is False  # 83.3%


def test_criterion_b_boundary():
    assert (
        check_criteria(fake_metrics(llm=26, base=23))["B"]["pass"] is True
    )  # +3 emails
    assert (
        check_criteria(fake_metrics(llm=26, base=24))["B"]["pass"] is False
    )  # +2 emails


def test_criterion_d_boundary():
    assert check_criteria(fake_metrics(with_result=29))["D"]["pass"] is True
    assert check_criteria(fake_metrics(with_result=28))["D"]["pass"] is False


def test_criterion_c_fails_when_auto_accepted_is_not_better():
    # 10/12 = 83.3% auto-accepted vs 27/30 = 90% overall
    assert check_criteria(fake_metrics(auto_k=10, auto_n=12))["C"]["pass"] is False


def test_criterion_c_inconclusive_with_few_auto_accepted():
    assert check_criteria(fake_metrics(auto_k=9, auto_n=9))["C"]["pass"] is None


# ---- dataset loading ----
def test_load_emails_by_split():
    assert len(load_emails(split="dev")) == 10
    assert len(load_emails(split="test")) == 30
    assert len(load_emails()) == 40

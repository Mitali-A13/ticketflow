from eval.baseline import classify


def test_baseline_obvious_cases():
    assert classify("", "I want a refund") == "refund"
    assert classify("Invoice", "wrong amount charged on my invoice") == "billing"
    assert classify("", "I forgot my password") == "account"
    assert classify("", "the app crashes") == "technical"
    assert classify("", "my parcel has not arrived") == "shipping"
    assert classify("", "Great service, thanks") == "other"


def test_baseline_refund_beats_billing():
    assert classify("", "I was charged twice, please refund me") == "refund"


def test_baseline_matches_word_starts_not_substrings():
    # "application" must not trigger the app rule, "happy" must not either
    assert classify("", "Thank you for your application, it was a happy day") == "other"

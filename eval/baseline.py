"""Keyword-rules baseline for category (the plan's baseline).
Run on the DEV split only:   python -m eval.baseline
The test split is scored by eval/run_eval.py in the final run, never here.
Edit these rules by looking at DEV results only."""

import re

from eval.scoring import load_emails

# Checked in this order (it mirrors the labeling rules); first match wins.
RULES = [
    ("refund", [r"refund", r"money back", r"\breturn"]),
    (
        "billing",
        [
            r"\bcharg",
            r"invoice",
            r"\bbill",
            r"payment",
            r"\bprice",
            r"coupon",
            r"\bfee\b",
            r"\bgst\b",
        ],
    ),
    (
        "account",
        [r"password", r"\blog ?in\b", r"\baccount", r"subscription", r"\bdelete"],
    ),
    (
        "technical",
        [
            r"crash",
            r"\bapp\b",
            r"error",
            r"freez",
            r"not working",
            r"stopped working",
            r"\bbug\b",
            r"website",
            r"\bsite\b",
            r"checkout",
            r"scan",
        ],
    ),
    (
        "shipping",
        [
            r"deliver",
            r"parcel",
            r"package",
            r"tracking",
            r"shipping",
            r"\barriv",
            r"address",
            r"\border\b",
        ],
    ),
]
COMPILED = [(cat, [re.compile(p) for p in pats]) for cat, pats in RULES]


def classify(subject: str, body: str) -> str:
    text = f"{subject}\n{body}".lower()
    for category, patterns in COMPILED:
        if any(p.search(text) for p in patterns):
            return category
    return "other"


def main() -> None:
    rows = load_emails(split="dev")
    correct = 0
    for r in rows:
        pred = classify(r["subject"], r["body"])
        gold = r["gold"]["category"]
        ok = pred == gold
        correct += ok
        print(f"{r['id']}  gold={gold:<10} pred={pred:<10} {'ok' if ok else 'WRONG'}")
    print(f"dev category accuracy: {correct}/{len(rows)}")


if __name__ == "__main__":
    main()

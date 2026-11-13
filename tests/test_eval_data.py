"""Guards the eval dataset against typos. They cannot tell if a label is wrong."""

import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "eval" / "emails.jsonl"
CATEGORIES = {"billing", "shipping", "refund", "technical", "account", "other"}
URGENCIES = {"low", "medium", "high"}
TAGS = {"clean", "rambling", "multi_issue", "no_order_id", "non_english", "injection"}


@pytest.fixture(scope="module")
def rows():
    lines = [ln for ln in DATA.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def text_of(row):
    return f"{row['subject']}\n{row['body']}"


def test_ids_unique_and_keys_present(rows):
    assert len(rows) == 40
    assert len({r["id"] for r in rows}) == 40
    for r in rows:
        assert {"id", "split", "tags", "sender", "subject", "body", "gold"} <= set(r)
        assert set(r["gold"]) == {"category", "urgency", "customer_name", "order_id"}


def test_split_sizes(rows):
    assert sum(r["split"] == "dev" for r in rows) == 10
    assert sum(r["split"] == "test" for r in rows) == 30


def test_gold_values_valid(rows):
    for r in rows:
        g = r["gold"]
        assert r["split"] in ("dev", "test"), r["id"]
        assert g["category"] in CATEGORIES, r["id"]
        assert g["urgency"] in URGENCIES, r["id"]
        assert g["customer_name"] is None or isinstance(g["customer_name"], str), r[
            "id"
        ]
        assert g["order_id"] is None or isinstance(g["order_id"], str), r["id"]
        assert set(r["tags"]) <= TAGS, r["id"]


def test_gold_order_id_literally_in_email(rows):
    for r in rows:
        if r["gold"]["order_id"] is not None:
            assert r["gold"]["order_id"] in text_of(r), r["id"]


def test_gold_customer_name_literally_in_email(rows):
    for r in rows:
        if r["gold"]["customer_name"] is not None:
            assert r["gold"]["customer_name"] in text_of(r), r["id"]


def test_no_order_id_tag_matches_null_gold(rows):
    for r in rows:
        assert ("no_order_id" in r["tags"]) == (r["gold"]["order_id"] is None), r["id"]


def test_tags_cover_both_splits(rows):
    for split in ("dev", "test"):
        seen = {t for r in rows if r["split"] == split for t in r["tags"]}
        assert TAGS <= seen, f"{split} is missing {TAGS - seen}"


def test_four_injection_emails(rows):
    injections = [r for r in rows if "injection" in r["tags"]]
    assert len(injections) == 4
    assert sum(r["split"] == "dev" for r in injections) == 1


def test_all_categories_in_both_splits(rows):
    for split in ("dev", "test"):
        seen = {r["gold"]["category"] for r in rows if r["split"] == split}
        assert seen == CATEGORIES, split

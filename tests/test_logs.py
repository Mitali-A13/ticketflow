import json
import logging

from app.logs import JsonFormatter


def test_json_formatter_includes_extras():
    record = logging.LogRecord(
        "worker", logging.WARNING, "f.py", 1, "job failed", (), None
    )
    record.job_id = 7
    data = json.loads(JsonFormatter().format(record))
    assert data["msg"] == "job failed" and data["level"] == "WARNING"
    assert data["job_id"] == 7 and "ts" in data

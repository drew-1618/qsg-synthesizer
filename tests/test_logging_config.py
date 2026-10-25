"""Unit tests for the JSON log formatter.

Note: pytest's `caplog` fixture (used elsewhere to assert on log *content*)
captures records through its own handler and formats them independently of
whatever's configured in qsg_synthesizer/logging_config.py, so it says nothing about the
actual on-disk/stdout output shape. These tests exercise JsonFormatter
directly to verify that shape.
"""

import json
import logging

from qsg_synthesizer.logging_config import JsonFormatter


def _make_record(level=logging.INFO, msg="hello %s", args=("world",), exc_info=None):
    return logging.LogRecord(
        name="qsg_synthesizer.services.example",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


def test_formats_as_single_line_json_with_expected_fields():
    formatter = JsonFormatter()
    line = formatter.format(_make_record())

    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "qsg_synthesizer.services.example"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload
    assert "exception" not in payload


def test_includes_exception_field_when_exc_info_present():
    formatter = JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = _make_record(
            level=logging.ERROR, msg="failed", args=(), exc_info=sys.exc_info()
        )

    payload = json.loads(formatter.format(record))
    assert payload["level"] == "ERROR"
    assert "RuntimeError: boom" in payload["exception"]

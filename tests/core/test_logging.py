"""Tests for structured JSON logging module."""

from __future__ import annotations

import io
import json
import logging

from msb_v3.core.logging import (
    HumanFormatter,
    JSONFormatter,
    configure_logging,
    get_logger,
)


def test_json_formatter_produces_valid_json():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "info"
    assert parsed["logger"] == "test"
    assert parsed["msg"] == "hello world"
    assert "ts" in parsed


def test_json_formatter_includes_exception():
    formatter = JSONFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=1,
        msg="operation failed",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert parsed["exception"]["type"] == "ValueError"
    assert parsed["exception"]["message"] == "test error"


def test_json_formatter_includes_structured_data():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="provider.selected",
        args=(),
        exc_info=None,
    )
    record.structured_data = {"provider": "paseo.claude", "latency_ms": 120}  # type: ignore[attr-defined]
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["provider"] == "paseo.claude"
    assert parsed["latency_ms"] == 120


def test_human_formatter_includes_level_and_name():
    formatter = HumanFormatter()
    record = logging.LogRecord(
        name="msb_v3.agent",
        level=logging.WARNING,
        pathname="test.py",
        lineno=1,
        msg="slow response",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    assert "WARNING" in output
    assert "msb_v3.agent" in output
    assert "slow response" in output


def test_configure_logging_json():
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    logger = get_logger("test.json")
    logger.info("test message")

    output = stream.getvalue()
    parsed = json.loads(output.strip())
    assert parsed["level"] == "info"
    assert parsed["msg"] == "test message"


def test_configure_logging_human():
    stream = io.StringIO()
    configure_logging(level="INFO", json_output=False, stream=stream)
    logger = get_logger("test.human")
    logger.info("test message")

    output = stream.getvalue()
    assert "INFO" in output
    assert "test message" in output


def test_get_logger_returns_standard_logger():
    logger = get_logger("msb_v3.test")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "msb_v3.test"


def test_json_formatter_debug_includes_source():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.DEBUG,
        pathname="test.py",
        lineno=42,
        msg="debug detail",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "source" in parsed
    assert parsed["source"]["line"] == 42
    assert parsed["source"]["func"] == record.funcName

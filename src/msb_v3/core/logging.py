"""Structured JSON logging — stdlib only, zero new dependencies.

Usage::

    from msb_v3.core.logging import configure_logging, get_logger

    configure_logging(level="INFO", json_output=True)
    logger = get_logger(__name__)
    logger.info("server.started", port=8766, host="127.0.0.1")

The JSON output format is one JSON object per line (NDJSON), suitable for
log aggregators, Loki, or manual inspection with ``jq``.

When ``json_output=False`` (the default), logs use a human-readable format
suitable for terminal use.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line (NDJSON)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Add structured extras if present
        if hasattr(record, "structured_data"):
            log_entry.update(record.structured_data)  # type: ignore[arg-type]

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        # Add source location for DEBUG
        if record.levelno <= logging.DEBUG:
            log_entry["source"] = {
                "file": record.filename,
                "line": record.lineno,
                "func": record.funcName,
            }

        return json.dumps(log_entry, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Human-readable format for terminal use."""

    COLORS = {
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = record.getMessage()

        line = f"{color}{ts} {record.levelname:<8}{self.RESET} {record.name}: {msg}"

        if record.exc_info and record.exc_info[0] is not None:
            line += f"\n{traceback.format_exception(*record.exc_info, chain=False)}"

        return line


def configure_logging(
    level: str = "INFO",
    json_output: bool = False,
    stream: Any | None = None,
) -> None:
    """Configure the root logger for structured output.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: If True, use JSON formatter. If False, use human-readable.
        stream: Output stream (defaults to stderr).
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(HumanFormatter())

    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger.

    Usage::

        logger = get_logger(__name__)
        logger.info("operation.completed", extra={"duration_ms": 42})

    For structured data, pass a ``structured_data`` extra::

        logger.info(
            "provider.selected",
            extra={"structured_data": {"provider": "paseo.claude", "latency_ms": 120}},
        )
    """
    return logging.getLogger(name)

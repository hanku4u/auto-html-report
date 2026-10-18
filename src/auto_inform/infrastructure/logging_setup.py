"""Structured logging: one JSON line per notable event.

The events and their fields are the contract in ``docs/07-configuration-and-logging.md`` §4. This module
only installs the formatter; the calls live where the events happen, so a block's start, retry, finish and
failure are logged by the code that knows about them.

``configure_logging`` is idempotent and replaces *its own* handlers rather than clearing the root
logger — a test or a host application may have installed handlers of its own, and silently removing those
would be rude and hard to debug.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Final

#: LogRecord attributes that are already represented, or are internal noise. Anything else on the record
#: came from ``extra=`` and belongs in the payload.
_RESERVED: Final = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)

#: Marks the handler this module installed, so a second call can replace it without touching others.
_OURS: Final = "_auto_inform_handler"


class JsonFormatter(logging.Formatter):
    """One object per line: timestamp, level, event name, then every structured field."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            # The traceback goes here, never into a report document.
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Readable single lines for a human at a terminal."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S")
        extras = " ".join(
            f"{key}={value}"
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        )
        line = f"{stamp} {record.levelname:<8} {record.getMessage()}"
        if extras:
            line = f"{line} {extras}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the structured handler on the root logger. Idempotent."""
    if fmt not in ("json", "console"):
        raise ValueError(f"unknown log format {fmt!r}; expected 'json' or 'console'")

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if getattr(handler, _OURS, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    setattr(handler, _OURS, True)
    root.addHandler(handler)

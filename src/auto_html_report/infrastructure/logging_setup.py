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

from auto_html_report.domain.errors import ConfigurationError

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
        # The formatter's own envelope keys. An `extra={"event": ...}` would otherwise silently rename
        # the event, which is the one thing a JSON log line exists to carry. Python's logging rejects
        # collisions with built-in LogRecord attributes, but these are ours, so nothing else would.
        "ts",
        "level",
        "event",
        "traceback",
    }
)

#: Marks the handler this module installed, so a second call can replace it without touching others.
_OURS: Final = "_auto_html_report_handler"

#: The level names this project accepts. One list, shared with :func:`load_settings`, so the loader and
#: the logging setup cannot disagree about what a valid level is -- they did, which is how `WARN` passed
#: one and `info` failed the other. Owned here because validating a level is a logging concern; the
#: loader importing it is the smaller oddity than `configure_logging` depending on `Settings`.
VALID_LOG_LEVELS: Final = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def normalise_log_level(value: str) -> str | None:
    """Upper-case and validate a level name, returning ``None`` when it is not one of ours."""
    normalised = value.strip().upper()
    return normalised if normalised in VALID_LOG_LEVELS else None


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


def _one_line(value: object) -> str:
    """Render one value so it cannot start a second line.

    Control characters are escaped rather than stripped, so the value stays readable and reversible:
    a newline in an upstream response body would otherwise produce a line that looks like another log
    record, and an ANSI escape could rewrite the terminal. ``isprintable()`` is the test, which leaves
    non-ASCII text alone. The JSON formatter does not need this -- ``json.dumps`` escapes already.
    """
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode("ascii")
        for character in str(value)
    )


class ConsoleFormatter(logging.Formatter):
    """Readable single lines for a human at a terminal.

    Every value goes through :func:`_one_line`, because the one format a human reads is also the one
    where a forged line does the most damage. The formatted traceback is still multi-line on purpose;
    it is emitted last, after the record, where a reader expects it.
    """

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S")
        extras = " ".join(
            f"{key}={_one_line(value)}"
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        )
        line = f"{stamp} {record.levelname:<8} {_one_line(record.getMessage())}"
        if extras:
            line = f"{line} {extras}"
        if record.exc_info:
            # Every traceback line is escaped *and* indented. A newline inside an exception message --
            # an upstream response body copied into a raise, say -- cannot be told apart from the
            # formatter's own line structure, so the whole block is marked as a block instead: no line
            # of it can begin where a record begins, and ANSI escapes inside one are neutralised.
            traceback_lines = self.formatException(record.exc_info).splitlines()
            block = "\n".join(f"    {_one_line(part)}" for part in traceback_lines)
            line = f"{line}\n{block}"
        return line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the structured handler on the root logger. Idempotent.

    Validates its own arguments and raises :class:`ConfigurationError` rather than letting
    ``logging.setLevel`` raise a raw ``ValueError`` from three frames away: this is configuration, and the
    project's hierarchy says configuration errors are catchable by one name.
    """
    if fmt not in ("json", "console"):
        raise ConfigurationError(f"unknown log format {fmt!r}; expected 'json' or 'console'")
    normalised_level = normalise_log_level(level)
    if normalised_level is None:
        raise ConfigurationError(
            f"unknown log level {level!r}; expected one of {', '.join(VALID_LOG_LEVELS)}"
        )

    root = logging.getLogger()
    root.setLevel(normalised_level)

    for handler in list(root.handlers):
        if getattr(handler, _OURS, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    setattr(handler, _OURS, True)
    root.addHandler(handler)

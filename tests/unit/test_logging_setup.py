"""Structured logging: the JSON line contract of ``docs/07`` §4, and handler installation.

The formatter tests build records directly, so they assert the payload without capturing a stream. The
installation tests save and restore the root logger, because ``configure_logging`` touches global state and
a leaked handler would make every later test's output surprising.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from auto_inform.domain.errors import ConfigurationError
from auto_inform.infrastructure.logging_setup import (
    ConsoleFormatter,
    JsonFormatter,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """Save and restore the root logger's level and handlers around each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _record(msg: str = "block.finish", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="auto_inform",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_should_emit_timestamp_level_and_event() -> None:
    # given a record with the fields the logging contract names
    record = _record(run_id="abc123", block="Maintenance history", status="OK", duration_ms=12.5)

    # when it is formatted
    payload = json.loads(JsonFormatter().format(record))

    # then the envelope and every structured field are present, so a log line answers "which block, how
    # long, which run" without a second query
    assert payload["level"] == "INFO"
    assert payload["event"] == "block.finish"
    assert payload["ts"].endswith("+00:00")
    assert payload["run_id"] == "abc123"
    assert payload["block"] == "Maintenance history"
    assert payload["status"] == "OK"
    assert payload["duration_ms"] == 12.5


def test_json_formatter_should_include_a_traceback_for_an_exception() -> None:
    # given a failure logged with its exception
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(error_type="ValueError")
        record.exc_info = sys.exc_info()

    # when it is formatted
    payload = json.loads(JsonFormatter().format(record))

    # then the traceback is in the log line -- and only there, never in a report document
    assert "ValueError: boom" in payload["traceback"]


def test_console_formatter_should_render_one_readable_line() -> None:
    # given the same record
    record = _record(block="Row", status="OK")

    # when the human-readable format is used
    line = ConsoleFormatter().format(record)

    # then the event and its fields are on a single line, which is what a person scanning a terminal wants
    assert "block.finish" in line
    assert "block=Row" in line
    assert "\n" not in line


def test_configure_logging_should_be_idempotent() -> None:
    # given logging configured twice, as a long-lived process or a test session may do
    configure_logging("INFO", "json")
    configure_logging("DEBUG", "console")

    # when the handlers are counted
    # then exactly one of ours is installed: the second call replaced the first rather than stacking
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_auto_inform_handler", False)]
    assert len(ours) == 1


def test_configure_logging_should_leave_foreign_handlers_alone() -> None:
    # given a host application that installed its own handler
    foreign = logging.StreamHandler(io.StringIO())
    logging.getLogger().addHandler(foreign)

    # when logging is configured
    configure_logging()

    # then the host's handler survives: silently removing somebody else's handler is rude and hard to
    # debug, so only our own marker is used to replace
    assert foreign in logging.getLogger().handlers


def test_configure_logging_should_set_the_level() -> None:
    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_with_an_unknown_format_should_raise_configuration_error() -> None:
    # given a format that is not one of the two documented values
    # then it is refused as a configuration error rather than silently defaulting, and it is catchable by
    # the project's base class rather than being a raw ValueError from three frames away
    with pytest.raises(ConfigurationError, match="unknown log format"):
        configure_logging(fmt="xml")


def test_configure_logging_with_an_unknown_level_should_raise_configuration_error() -> None:
    # given a level logging does not know
    # then it is refused as a configuration error, not as logging's own ValueError
    with pytest.raises(ConfigurationError, match="unknown log level"):
        configure_logging(level="CHATTY")


def test_json_formatter_extras_should_not_clobber_the_envelope() -> None:
    # given a caller that supplies an extra named after one of the formatter's own keys
    record = _record(event="clobbered", level="CLOBBERED", ts="CLOBBERED")

    # when it is formatted
    payload = json.loads(JsonFormatter().format(record))

    # then the envelope wins: losing the event name is losing the one thing a JSON log line carries, and
    # Python's logging only rejects collisions with its own LogRecord attributes, not with ours
    assert payload["event"] == "block.finish"
    assert payload["level"] == "INFO"
    assert payload["ts"] != "CLOBBERED"


@pytest.mark.parametrize(
    ("level", "accepted"),
    [("info", True), ("Info", True), ("WARN", False), ("NOTSET", False), ("CHATTY", False)],
)
def test_configure_logging_should_accept_the_same_levels_as_the_loader(
    level: str, accepted: bool
) -> None:
    # given the level names this project accepts, which live in one shared definition
    # when configure_logging is asked for one
    # then it agrees with load_settings about it: the two used to disagree, accepting `info` in one and
    # `WARN` in the other, which made neither authoritative
    if accepted:
        configure_logging(level=level)
        assert logging.getLogger().level == logging.INFO
    else:
        with pytest.raises(ConfigurationError, match="unknown log level"):
            configure_logging(level=level)


def test_console_formatter_should_escape_control_characters_in_values() -> None:
    # given a record whose fields came from an upstream API, complete with a forged line and an ANSI
    # escape
    record = _record(reason="line one\nINFO forged", run_id="x\x1b[31mred")

    # when it is formatted for a terminal
    line = ConsoleFormatter().format(record)

    # then nothing starts a second line and the escape is visible text: a response body embedded in an
    # exception message cannot masquerade as another log record
    assert "\n" not in line
    assert "reason=line one\\nINFO forged" in line
    assert "run_id=x\\x1b[31mred" in line


def test_console_formatter_should_keep_a_multiline_message_on_one_line() -> None:
    # given an event name that itself contains a newline
    record = _record(msg="line one\nINFO forged")

    # when it is formatted
    # then the message is escaped too, not just the extra fields
    assert "\n" not in ConsoleFormatter().format(record)


def test_console_formatter_should_indent_a_traceback_so_it_cannot_forge_a_record() -> None:
    # given a failure whose message contains a newline, the shape an upstream response body takes when a
    # factory puts it into an exception
    import sys

    try:
        raise ValueError("line one\nINFO forged")
    except ValueError:
        record = _record(error_type="ValueError")
        record.exc_info = sys.exc_info()

    # when it is formatted for a terminal
    lines = ConsoleFormatter().format(record).splitlines()

    # then the record is the only line that starts at column zero: every traceback line, including the
    # one a newline in the message produced, is indented, so a log collector cannot mistake it for
    # another record
    assert lines[0].startswith(tuple("0123456789"))
    assert all(line.startswith("    ") for line in lines[1:])
    assert not any(line.startswith("INFO forged") for line in lines)

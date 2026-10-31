"""Configuration refusals for the PostgreSQL store, and what its errors may say.

These need no database, so they run in every CI: a wrong-backend settings object must be refused, and a
malformed DSN must not put a password fragment into an exception that gets logged.
"""

from __future__ import annotations

import pytest

from auto_html_report.domain.errors import ConfigurationError, StoreError
from auto_html_report.infrastructure.settings import Settings
from auto_html_report.infrastructure.stores.postgres_store import PostgresEventStore, _redact


def test_from_settings_with_sqlite_selected_should_refuse() -> None:
    # given settings that name the other backend
    # when a PostgreSQL store is built from them
    # then it refuses: writing to the wrong backend is worse than failing at start-up
    with pytest.raises(ConfigurationError, match="is not 'postgres'"):
        PostgresEventStore.from_settings(Settings(event_store="sqlite"))


def test_from_settings_without_a_dsn_should_raise_configuration_error() -> None:
    # given a directly constructed Settings with no DSN (the loader would have refused it)
    with pytest.raises(ConfigurationError, match="POSTGRES_DSN"):
        PostgresEventStore.from_settings(Settings(event_store="postgres"))


def test_a_malformed_dsn_should_not_echo_a_password_fragment() -> None:
    # given a keyword DSN whose password contains a space -- libpq reports the token *after* the space
    # when it fails to parse, which is a fragment of the secret. The driver is the optional extra, so a
    # base install skips this rather than failing; CI installs every extra (docs/09 §5).
    pytest.importorskip("psycopg")
    with pytest.raises(StoreError, match="malformed") as raised:
        PostgresEventStore("host=localhost user=test password=pa ss dbname=x")

    # then the message is generic, and there is no chained driver text to leak it either
    message = str(raised.value)
    assert "missing" not in message
    assert 'after "ss"' not in message
    assert raised.value.__cause__ is None


def test_redaction_should_mask_the_dsn_verbatim() -> None:
    assert (
        _redact("could not use postgresql://u:p@h/d", "postgresql://u:p@h/d") == "could not use ***"
    )


def test_redaction_should_mask_uri_userinfo() -> None:
    assert _redact("postgresql://user:s3cret@host/db failed", "") == (
        "postgresql://user:***@host/db failed"
    )


def test_redaction_should_mask_a_keyword_password() -> None:
    redacted = _redact("password='hidden value' host=x", "")
    assert "hidden" not in redacted

"""PostgreSQL-specific behaviour: raw-column properties and configuration refusals.

The shared suite (``test_store_behaviour.py``) runs the same cases against this backend when
``AUTO_HTML_REPORT_TEST_POSTGRES_DSN`` is set. What is left here is what the port cannot observe: the two
properties docs/04 names but exposes no read-by-id method for, plus the ways a store can be misconfigured.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any, cast

import pytest

from auto_html_report.domain.errors import StoreError
from auto_html_report.infrastructure.stores.postgres_store import PostgresEventStore
from tests.support.builders import make_event
from tests.support.postgres import postgres_test_dsn

pytestmark = pytest.mark.postgres


@pytest.fixture
def dsn() -> str:
    """A DSN for the suite's own schema, with an empty table."""
    return postgres_test_dsn()


def _connect(dsn: str) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn, row_factory=dict_row)


def _row(dsn: str, event_id: str) -> dict[str, Any]:
    with _connect(dsn) as connection:
        row = connection.execute(
            "SELECT event_type, event_id, payload, status, attempts, last_error, output_uri "
            "FROM events WHERE event_id = %s",
            (event_id,),
        ).fetchone()
    return cast("dict[str, Any]", row)


def test_the_repr_should_not_contain_the_dsn(dsn: str) -> None:
    # given a store connected with a DSN that carries a password

    # when it is represented -- a debugging session will do this
    with PostgresEventStore(dsn) as store:
        representation = repr(store)

    # then the password cannot leak into a log line
    assert dsn not in representation
    assert "***" in representation


def test_save_should_not_overwrite_a_processed_rows_payload(dsn: str) -> None:
    # given a delivered row
    original = make_event("a", payload={"lot": "L1"})
    with PostgresEventStore(dsn) as store:
        store.save([original])
        store.mark_processed(original, output_uri="s3://bucket/a.html")

        # when re-discovery presents the same key with a different payload
        store.save([make_event("a", payload={"lot": "CHANGED"})])

    # then the delivered row is untouched, including its payload: insert-only means insert-only
    row = _row(dsn, "a")
    assert row["payload"] == {"lot": "L1"}
    assert row["status"] == "PROCESSED"


def test_mark_processed_should_clear_a_previous_error(dsn: str) -> None:
    # given a row that failed once and then succeeded
    event = make_event("a")
    with PostgresEventStore(dsn) as store:
        store.save([event])
        store.mark_failed(event, reason="upstream 503")
        store.mark_processed(event, output_uri="s3://bucket/a.html")

    # then the failure no longer describes the row
    row = _row(dsn, "a")
    assert row["status"] == "PROCESSED"
    assert row["last_error"] is None
    assert row["output_uri"] == "s3://bucket/a.html"


def test_mark_failed_should_record_the_reason_while_pending(dsn: str) -> None:
    # given a first failed attempt with the default limit of three
    event = make_event("a")
    with PostgresEventStore(dsn) as store:
        store.save([event])
        record = store.mark_failed(event, reason="upstream 503")

    # then the row is NEW with one attempt and the reason, and the store reported the same count
    row = _row(dsn, "a")
    assert (row["status"], row["attempts"], row["last_error"]) == ("NEW", 1, "upstream 503")
    assert (record.attempts, record.retired) == (1, False)


# --- configuration refusals (no database needed) -------------------------------


def test_a_non_utc_session_should_still_return_utc_timestamps() -> None:
    # given a session whose timezone is not UTC, so TIMESTAMPTZ decodes with a +02:00 offset
    dsn = postgres_test_dsn(timezone="Europe/Berlin")
    event = make_event("a")

    # when it round-trips through the store
    with PostgresEventStore(dsn) as store:
        store.save([event])
        (returned,) = store.pending()

    # then the instant is unchanged and the timezone is UTC, as docs/04 §1 requires: a report must print
    # Z, not the server session's offset
    assert returned.discovered_at == event.discovered_at
    assert returned.discovered_at.tzinfo == UTC


def test_pending_should_leave_the_connection_idle(dsn: str) -> None:
    # given an open store
    with PostgresEventStore(dsn) as store:
        # when the queue is read
        store.pending()

        # then no transaction is left open: an idle-in-transaction session holds a snapshot and a lock,
        # and a failed read would leave the session aborted for the next call
        assert store._require_open().info.transaction_status.name == "IDLE"


def test_a_wrong_password_should_be_reported_without_the_password(dsn: str) -> None:
    # given a DSN whose password is wrong -- replaced structurally, so the test works whatever credentials
    # the operator's DSN carries
    from psycopg.conninfo import make_conninfo

    wrong = make_conninfo(dsn, password="not-the-password")

    # when the store connects
    # then the failure is reported, and the password is not in the message
    with pytest.raises(StoreError, match="authentication") as raised:
        PostgresEventStore(wrong)
    assert "not-the-password" not in str(raised.value)

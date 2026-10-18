"""SQLite-specific behaviour: path resolution, storage format, and the thread rule.

``test_store_behaviour.py`` holds the cases that describe the *port*. These are the ones that can only
be asserted against SQLite itself: the default file location derived from settings, the JSON text
column, WAL mode, and the fact that the connection refuses another thread with a message that names
the rule rather than a raw ``sqlite3.ProgrammingError``.

Several cases read the table with a second, plain connection. That is deliberate: the port exposes no
read-by-id method, so a property about a column (a payload not being overwritten, ``last_error`` being
cleared) has to be observed at the storage layer. See the note at the top of the shared suite.
"""

from __future__ import annotations

import sqlite3
import stat
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_inform.domain.errors import ConfigurationError, StoreError
from auto_inform.infrastructure.settings import Settings
from auto_inform.infrastructure.stores.sqlite_store import SqliteEventStore
from tests.support.builders import make_event
from tests.support.clocks import DEFAULT_START, FakeClock


def _rows(path: Path) -> list[sqlite3.Row]:
    """Read the stored rows directly, with an ordinary connection.

    ``ORDER BY event_id`` so assertions about a named row are stable regardless of insertion order.
    """
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute("SELECT * FROM events ORDER BY event_id").fetchall()
    finally:
        connection.close()


# --- construction and path resolution ----------------------------------------


def test_from_settings_without_a_path_should_use_the_output_directory(tmp_path: Path) -> None:
    # given settings that name only an output directory
    settings = Settings(output_dir=tmp_path)

    # when the store is built from them
    with SqliteEventStore.from_settings(settings) as store:
        store.save([make_event("a")])

    # then the default path sits beside the artefacts it describes (docs/04 §4)
    assert (tmp_path / "events.sqlite3").is_file()


def test_from_settings_should_honour_an_explicit_sqlite_path(tmp_path: Path) -> None:
    # given an explicit path elsewhere
    explicit = tmp_path / "state" / "events.sqlite3"
    settings = Settings(output_dir=tmp_path / "reports", sqlite_path=explicit)

    # when the store is built
    with SqliteEventStore.from_settings(settings) as store:
        store.save([make_event("a")])

    # then it is the file used, and its directory was created
    assert explicit.is_file()


def test_from_settings_with_postgres_selected_should_refuse_to_write_a_local_file() -> None:
    # given settings that ask for PostgreSQL
    settings = Settings(event_store="postgres", postgres_dsn="postgresql://user@host/db")

    # when the SQLite store is built from them
    # then it refuses: a job that believes it is writing to PostgreSQL and is not is worse than one
    # that fails at start-up
    with pytest.raises(ConfigurationError, match="PostgreSQL store arrives with stage 11"):
        SqliteEventStore.from_settings(settings)


def test_from_settings_should_pass_the_configured_attempt_limit(tmp_path: Path) -> None:
    # given settings allowing exactly one attempt
    settings = Settings(output_dir=tmp_path, event_max_attempts=1)
    with SqliteEventStore.from_settings(settings) as store:
        event = make_event("a")
        store.save([event])

        # when that one attempt fails
        store.mark_failed(event, reason="boom")

        # then the event is retired immediately, which is what a limit of one means
        assert store.pending() == []


def test_max_attempts_below_one_should_raise_configuration_error(tmp_path: Path) -> None:
    # given a limit that would retire every event on its first failure
    # when a store is constructed with it
    # then the mistake is reported at construction, not as a mysteriously empty queue later
    with pytest.raises(ConfigurationError, match="at least 1"):
        SqliteEventStore(tmp_path / "events.sqlite3", max_attempts=0)


# --- storage format -----------------------------------------------------------


def test_payload_and_timestamp_should_round_trip_through_json_and_iso_text(
    tmp_path: Path,
) -> None:
    # given an event with a nested, non-ASCII payload and a non-UTC timestamp
    event = make_event(
        "a",
        payload={"lot": "Löt-1", "measurements": [1, 2, 3], "nested": {"ok": True}},
        discovered_at=datetime(2026, 10, 18, 14, 0, tzinfo=UTC).astimezone(
            timezone(timedelta(hours=2))
        ),
    )

    # when it is saved and read back
    with SqliteEventStore(tmp_path / "events.sqlite3") as store:
        store.save([event])
        (returned,) = store.pending()

    # then the payload is intact and the timestamp is the same instant, in UTC
    assert returned.payload == event.payload
    assert returned.discovered_at == event.discovered_at
    assert returned.discovered_at.tzinfo == UTC


def test_save_with_a_naive_timestamp_should_raise_and_persist_nothing(tmp_path: Path) -> None:
    # given an event stamped with a naive datetime
    naive = make_event("a", discovered_at=datetime(2026, 10, 18, 12, 0))

    # when a batch containing it is saved
    with SqliteEventStore(tmp_path / "events.sqlite3") as store:
        with pytest.raises(ValueError, match="naive"):
            store.save([make_event("ok"), naive])

        # then nothing from the batch landed: the whole save is one transaction, and a timestamp whose
        # offset is unknown would make the queue's oldest-first order depend on the host's timezone
        assert store.pending() == []


def test_save_should_not_overwrite_a_processed_rows_payload(tmp_path: Path) -> None:
    # given a delivered event whose row carries its original payload
    path = tmp_path / "events.sqlite3"
    original = make_event("a", payload={"lot": "L1"})
    with SqliteEventStore(path) as store:
        store.save([original])
        store.mark_processed(original, output_uri="file:///reports/a.html")

        # when re-discovery presents the same key with a different payload
        store.save([make_event("a", payload={"lot": "CHANGED"})])

    # then the delivered row is untouched -- payload and status. Rewriting the inputs of a report that
    # has already been shipped is worse than making an operator delete the row (docs/04 §2.1)
    (row,) = _rows(path)
    assert row["payload"] == '{"lot":"L1"}'
    assert row["status"] == "PROCESSED"


def test_mark_processed_should_clear_a_previous_error_and_record_the_uri(tmp_path: Path) -> None:
    # given an event that failed once and then succeeded
    path = tmp_path / "events.sqlite3"
    event = make_event("a")
    clock = FakeClock(start=DEFAULT_START)
    with SqliteEventStore(path, clock=clock) as store:
        store.save([event])
        store.mark_failed(event, reason="upstream 503")
        store.mark_processed(event, output_uri="file:///reports/a.html")

    # then the failure no longer describes the row, and the delivery is recorded with its timestamp
    (row,) = _rows(path)
    assert row["status"] == "PROCESSED"
    assert row["last_error"] is None
    assert row["output_uri"] == "file:///reports/a.html"
    assert row["processed_at"] == DEFAULT_START.isoformat()


def test_opening_an_existing_path_should_keep_its_events(tmp_path: Path) -> None:
    # given a store that was written and closed
    path = tmp_path / "events.sqlite3"
    with SqliteEventStore(path) as store:
        store.save([make_event("a")])

    # when a new store is opened on the same file
    with SqliteEventStore(path) as reopened:
        # then the idempotent DDL did not reset anything
        assert [event.event_id for event in reopened.pending()] == ["a"]


def test_the_journal_mode_should_be_wal(tmp_path: Path) -> None:
    # given an open store
    path = tmp_path / "events.sqlite3"
    with SqliteEventStore(path):
        connection = sqlite3.connect(path)
        try:
            # then WAL is on, so the processing job can read while discovery holds a write
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()


# --- lifecycle misuse ---------------------------------------------------------


def test_mark_processed_twice_should_raise_store_error(tmp_path: Path) -> None:
    # given an event already marked processed
    event = make_event("a")
    with SqliteEventStore(tmp_path / "events.sqlite3") as store:
        store.save([event])
        store.mark_processed(event, output_uri="file:///reports/a.html")

        # when it is marked again
        # then the second transition is refused: lifecycle moves are explicit and single-direction
        with pytest.raises(StoreError, match="not a NEW event"):
            store.mark_processed(event, output_uri="file:///reports/again.html")


def test_use_after_close_should_raise_store_error(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    store.close()

    with pytest.raises(StoreError, match="closed"):
        store.pending()


def test_using_the_store_from_another_thread_should_raise_a_store_error_naming_the_rule(
    tmp_path: Path,
) -> None:
    # given a store opened on this thread
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    failures: list[BaseException] = []

    def use_from_another_thread() -> None:
        try:
            store.save([make_event("a")])
        except Exception as exc:  # noqa: BLE001 - the point is to capture what the store raises
            failures.append(exc)

    # when another thread tries to save
    worker = threading.Thread(target=use_from_another_thread)
    worker.start()
    worker.join()
    store.close()

    # then it fails as a StoreError whose message names the thread rule, not as a raw
    # sqlite3.ProgrammingError that a caller would have to know to expect (docs/04 §2.4)
    assert len(failures) == 1
    assert isinstance(failures[0], StoreError)
    assert "thread-affine" in str(failures[0])


def test_pending_with_a_negative_limit_should_raise_value_error(tmp_path: Path) -> None:
    with (
        SqliteEventStore(tmp_path / "events.sqlite3") as store,
        pytest.raises(ValueError, match="must not be negative"),
    ):
        store.pending(limit=-1)


def test_mark_failed_should_record_the_reason_while_the_event_is_still_pending(
    tmp_path: Path,
) -> None:
    # given an event whose first attempt failed
    path = tmp_path / "events.sqlite3"
    event = make_event("a")
    with SqliteEventStore(path, max_attempts=2) as store:
        store.save([event])
        store.mark_failed(event, reason="upstream 503")

    # then the row says NEW with one attempt and the reason. The port cannot show last_error, so this is
    # observed at the storage layer -- but deleting the column write would otherwise leave the suite green
    (row,) = _rows(path)
    assert (row["status"], row["attempts"], row["last_error"]) == ("NEW", 1, "upstream 503")


def test_a_new_store_file_should_be_owner_only(tmp_path: Path) -> None:
    # given a fresh store path with a typical umask
    path = tmp_path / "events.sqlite3"

    # when the store is created
    with SqliteEventStore(path):
        pass

    # then the file is 0600, matching the policy for the reports it holds the inputs of (docs/06 §4).
    # SQLite would otherwise create it 0644 & ~umask, readable by every local account
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

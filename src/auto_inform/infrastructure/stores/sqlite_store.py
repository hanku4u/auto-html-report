"""The default event store: one SQLite file, insert-only on a stable conflict key.

``docs/04-events-and-stores.md`` is normative for the semantics; this module is the implementation.
Three properties are worth knowing before reading it:

**Insert-only, deliberately.** ``save()`` never updates an existing row, not even to refresh its
payload. Re-discovery of a rolling window therefore reports yesterday's events as duplicates instead of
resetting work already done, and a malformed payload persists until an operator deletes the row. The
alternative -- overwriting a ``PROCESSED`` row's inputs -- would rewrite the inputs of a report that has
already been delivered, which is worse than a ``DELETE`` (``docs/04`` §2.1).

**Retirement happens inside one statement.** ``mark_failed`` increments ``attempts`` and decides
``NEW`` versus ``FAILED`` in the same ``UPDATE``. Reading the count, deciding, then writing would give a
window in which two failures both see ``attempts = max_attempts - 1`` and neither retires the event,
which is precisely the starvation ADR-0019 exists to prevent.

**Thread affinity is enforced, not requested.** The connection keeps SQLite's default
``check_same_thread=True``, so a store used from another thread fails loudly with a message naming the
rule instead of corrupting state in a way that surfaces later. The processing job marshals its store
calls onto the main thread for this reason (``docs/04`` §2.4).

**File modes are POSIX semantics**, like the report writer's (``docs/06`` §1): a new database is created
0600 and a new directory 0700, and ``O_NOFOLLOW`` is applied where the platform provides it. Windows is
not a supported target for this store either.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Self

from auto_inform.application.ports import Clock, FailureRecord, SaveResult
from auto_inform.domain.errors import ConfigurationError, StoreError
from auto_inform.domain.event import Event, EventStatus
from auto_inform.infrastructure.clock import SystemClock
from auto_inform.infrastructure.settings import Settings

#: Created if absent and never altered. There is no migration framework in v1: a schema change is a
#: reviewed operation, not something that happens implicitly on open (``docs/04`` §3).
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS events (
    event_type    TEXT        NOT NULL,
    event_id      TEXT        NOT NULL,
    payload       TEXT        NOT NULL,
    discovered_at TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    status        TEXT        NOT NULL,
    attempts      INTEGER     NOT NULL DEFAULT 0,
    last_error    TEXT,
    output_uri    TEXT,
    processed_at  TEXT,
    PRIMARY KEY (event_type, event_id)
);
CREATE INDEX IF NOT EXISTS events_pending_idx
    ON events (status, event_type, discovered_at);
"""


def _as_utc_text(value: datetime) -> str:
    """Normalise to UTC ISO-8601, so stored timestamps sort lexicographically as they do in time.

    A naive timestamp is rejected rather than assumed to be local time: assuming would make the same
    event sort differently on two hosts, and the queue's "oldest first" ordering rests on this column.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"timestamp {value!r} is naive; events carry timezone-aware UTC timestamps"
        )
    return value.astimezone(UTC).isoformat()


class SqliteEventStore:
    """A single-file event store. Use it as a context manager so the file handle is released."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_attempts: int = 3,
        clock: Clock | None = None,
    ) -> None:
        """Open ``path``, creating the file, its directory and the schema if they do not exist."""
        if max_attempts < 1:
            # Zero would retire every event on its first failure before any retry, which is a
            # configuration mistake that looks like a mysterious queue. Reject it at construction.
            raise ConfigurationError(f"max_attempts is {max_attempts!r}; it must be at least 1")

        self._path = Path(path)
        self._max_attempts = max_attempts
        self._clock = clock if clock is not None else SystemClock()
        self._connection: sqlite3.Connection | None = None
        self._open()

    @classmethod
    def from_settings(cls, settings: Settings, *, clock: Clock | None = None) -> SqliteEventStore:
        """Build the store the settings ask for, resolving the default path.

        ``AUTO_INFORM_SQLITE_PATH`` defaults to ``<output_dir>/events.sqlite3`` -- a derived default, so
        it cannot be a plain field default on :class:`Settings` (``docs/04`` §4).

        ``AUTO_INFORM_EVENT_STORE=postgres`` is refused rather than silently producing a local file: a
        job that believes it is writing to PostgreSQL and is not is worse than a job that fails at
        start-up. The PostgreSQL store arrives with stage 11.
        """
        if settings.event_store != "sqlite":
            raise ConfigurationError(
                f"AUTO_INFORM_EVENT_STORE={settings.event_store} is not implemented yet; the PostgreSQL "
                "store arrives with stage 11. Use AUTO_INFORM_EVENT_STORE=sqlite for now."
            )

        path = (
            settings.sqlite_path
            if settings.sqlite_path is not None
            else settings.output_dir / "events.sqlite3"
        )
        return cls(path, max_attempts=settings.event_max_attempts, clock=clock)

    # --- the port -------------------------------------------------------------

    def save(self, events: Iterable[Event]) -> SaveResult:
        """Insert events, leaving existing rows untouched. Idempotent on ``(event_type, event_id)``.

        The whole batch is one transaction: a payload that cannot be serialised rolls the batch back
        rather than persisting the events that happened to come before it.
        """
        new = 0
        duplicates = 0
        with self._transaction():
            for event in events:
                cursor = self._execute(
                    "INSERT INTO events "
                    "(event_type, event_id, payload, discovered_at, source, status) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (event_type, event_id) DO NOTHING",
                    (
                        event.event_type,
                        event.event_id,
                        json.dumps(dict(event.payload), sort_keys=True, separators=(",", ":")),
                        _as_utc_text(event.discovered_at),
                        event.source,
                        EventStatus.NEW.value,
                    ),
                )
                if cursor.rowcount == 1:
                    new += 1
                    continue

                # A duplicate is only a duplicate if it came from the same source. Two discoverers
                # sharing an event_type (a supported configuration) and reusing an event_id would
                # otherwise let one silently suppress the other's event for ever: the row is insert-only,
                # so the loser's payload is never recorded and the only trace is a duplicate count.
                stored = self._execute(
                    "SELECT source FROM events WHERE event_type = ? AND event_id = ?",
                    (event.event_type, event.event_id),
                ).fetchone()
                if stored is not None and stored["source"] != event.source:
                    raise StoreError(
                        f"{event.event_type}/{event.event_id} is already stored by source "
                        f"{stored['source']!r}, not {event.source!r}. An event_id must be unique within "
                        "its event_type, so two sources cannot reuse one (docs/04 §2.1)."
                    )
                duplicates += 1

        return SaveResult(new=new, duplicates=duplicates)

    def pending(
        self,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """``NEW`` events under the attempt limit, oldest first, optionally filtered and capped.

        The tie-break on ``(event_type, event_id)`` makes the order total, so two events discovered in
        the same instant cannot swap places between runs -- a flaky test, and worse, a flaky support
        answer.
        """
        if limit is not None and limit < 0:
            raise ValueError(f"limit must not be negative; got {limit!r}")

        sql = [
            "SELECT event_type, event_id, payload, discovered_at, source FROM events",
            "WHERE status = ? AND attempts < ?",
        ]
        parameters: list[object] = [EventStatus.NEW.value, self._max_attempts]
        if event_type is not None:
            sql.append("AND event_type = ?")
            parameters.append(event_type)
        sql.append("ORDER BY discovered_at, event_type, event_id")
        if limit is not None:
            sql.append("LIMIT ?")
            parameters.append(limit)

        cursor = self._execute(" ".join(sql), tuple(parameters))
        return [self._event_from_row(row) for row in cursor.fetchall()]

    def mark_processed(self, event: Event, *, output_uri: str) -> None:
        """Transition a ``NEW`` event to ``PROCESSED``, recording where its document landed.

        ``last_error`` is cleared: a later success means the earlier failure no longer describes the
        event's state, and keeping it would make a healthy event look broken (``docs/04`` §2.3).
        Called only after the writer has returned, never before (``docs/05`` §3).
        """
        with self._transaction():
            cursor = self._execute(
                "UPDATE events "
                "SET status = ?, processed_at = ?, output_uri = ?, last_error = NULL "
                "WHERE event_type = ? AND event_id = ? AND status = ?",
                (
                    EventStatus.PROCESSED.value,
                    _as_utc_text(self._clock.now()),
                    output_uri,
                    event.event_type,
                    event.event_id,
                    EventStatus.NEW.value,
                ),
            )
            if cursor.rowcount != 1:
                raise StoreError(
                    f"cannot mark {event.event_type}/{event.event_id} processed: it is not a NEW event "
                    "in this store (already processed, retired, or never saved)"
                )

    def mark_failed(self, event: Event, *, reason: str) -> FailureRecord:
        """Count one failed attempt, retiring the event at ``max_attempts``.

        The increment and the retirement test happen in a single ``UPDATE`` so they cannot race: the
        ``CASE`` reads the pre-update ``attempts`` value, which is exactly the count this failure
        brings to ``max_attempts`` (ADR-0019). The row is read back inside the same transaction, so the
        count the caller logs is the count that was committed, not a second guess.
        """
        with self._transaction():
            cursor = self._execute(
                "UPDATE events "
                "SET attempts = attempts + 1, "
                "    status = CASE WHEN attempts + 1 >= ? THEN ? ELSE ? END, "
                "    last_error = ? "
                "WHERE event_type = ? AND event_id = ? AND status = ?",
                (
                    self._max_attempts,
                    EventStatus.FAILED.value,
                    EventStatus.NEW.value,
                    reason,
                    event.event_type,
                    event.event_id,
                    EventStatus.NEW.value,
                ),
            )
            if cursor.rowcount != 1:
                raise StoreError(
                    f"cannot record a failure for {event.event_type}/{event.event_id}: it is not a NEW "
                    "event in this store (already processed, retired, or never saved)"
                )
            row = self._execute(
                "SELECT attempts, status FROM events WHERE event_type = ? AND event_id = ?",
                (event.event_type, event.event_id),
            ).fetchone()

        if row is None:  # pragma: no cover - the UPDATE above matched this row in this transaction
            raise StoreError(
                f"event {event.event_type}/{event.event_id} disappeared during mark_failed"
            )
        return FailureRecord(
            attempts=row["attempts"],
            retired=row["status"] == EventStatus.FAILED.value,
        )

    def close(self) -> None:
        """Release the connection. Safe to call twice, and safe after an exception."""
        connection = self._connection
        if connection is None:
            return
        try:
            connection.close()
        except sqlite3.Error as exc:
            raise StoreError(f"could not close the event store at {self._path}: {exc}") from exc
        self._connection = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if exc_info[0] is None:
            self.close()
            return
        try:
            self.close()
        except StoreError:
            # The failure that brought us here is the diagnosis; a close that fails afterwards is a
            # second symptom and must not replace it.
            pass

    # --- internals ------------------------------------------------------------

    def _open(self) -> None:
        """Create the directory, open the file, and apply the idempotent DDL."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # SQLite creates the database 0644 & ~umask and gives -wal/-shm the same mode. Reports are
            # published owner-only deliberately (docs/06 §4); a store readable by every local account
            # would leak the inputs those reports were built from. Create a new file 0600; an existing
            # file keeps whatever mode its operator chose.
            try:
                descriptor = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            connection = sqlite3.connect(self._path)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"could not open the event store at {self._path}: {exc}") from exc

        try:
            connection.row_factory = sqlite3.Row
            # WAL lets a reader proceed while the store holds a write, which matters because the
            # discovery and processing jobs may overlap (docs/04 §4).
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            connection.commit()
        except sqlite3.Error as exc:
            # Do not leak the handle on the path where initialisation failed.
            connection.close()
            raise StoreError(
                f"could not initialise the event store at {self._path}: {exc}"
            ) from exc

        self._connection = connection

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Commit on success, roll back on failure, translating either failure like any other.

        Not ``with connection:``: SQLite's own context manager commits in ``__exit__``, and a commit
        attempted from the wrong thread raises ``ProgrammingError`` *there* -- replacing the
        ``StoreError`` that explained the real problem with a raw driver error. Wrapping both sides
        keeps the diagnosis, and a rollback that cannot run is swallowed rather than allowed to mask
        the original failure.
        """
        connection = self._require_open()
        try:
            yield
            # sqlite3 opens a transaction implicitly on the first DML. An empty batch has none, and a
            # COMMIT would then fail instead of being the no-op an empty save is.
            if connection.in_transaction:
                self._execute("COMMIT")
        except BaseException:
            # Covers a failed COMMIT as well as a failed statement: SQLite keeps a failed transaction
            # open, and leaving it open would make the *next* call's commit swallow both batches.
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise

    def _require_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StoreError(
                f"the event store at {self._path} is closed; construct one per job run (docs/04 §2.5)"
            )
        return self._connection

    def _execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        """Run one statement, translating SQLite's errors into the framework's vocabulary."""
        connection = self._require_open()
        try:
            return connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            hint = ""
            if "thread" in str(exc):
                hint = (
                    "; SQLite connections are thread-affine, so a store must be used from the thread "
                    "that opened it (docs/04 §2.4)"
                )
            raise StoreError(f"event store error: {exc}{hint}") from exc

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        key = f"{row['event_type']}/{row['event_id']}"
        try:
            payload = json.loads(row["payload"])
            discovered_at = datetime.fromisoformat(row["discovered_at"])
        except (TypeError, ValueError) as exc:
            # ``StoreError`` is documented as "unreachable or corrupt", and a raw JSONDecodeError slips
            # past every caller that catches the hierarchy. The key is in the message so an operator can
            # find the row an editor or a bad upgrade damaged.
            raise StoreError(f"corrupt event row {key}: {exc}") from exc
        return Event(
            event_type=row["event_type"],
            event_id=row["event_id"],
            payload=payload,
            discovered_at=discovered_at,
            source=row["source"],
        )

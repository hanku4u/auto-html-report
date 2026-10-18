"""The optional PostgreSQL event store: the same port, the same rules, a shared database.

``docs/04-events-and-stores.md`` is normative; this module is one implementation of it. The semantics are
deliberately identical to the SQLite store — insert-only ``save``, oldest-first ``pending`` with retirement,
single-direction lifecycle transitions — because a job must not change meaning when an operator switches
backends. What differs is the storage:

* ``payload`` is ``JSONB``, so PostgreSQL rejects invalid JSON at write time. The SQLite store can hold a
  damaged row and reports it as ``StoreError``; here that state cannot exist.
* ``discovered_at`` and ``processed_at`` are ``TIMESTAMPTZ``, so ordering is done by the database in time
  rather than by lexicographic order over normalised text.
* ``mark_failed`` still increments and decides retirement in **one** ``UPDATE`` (ADR-0019), then reads the
  committed row back inside the same transaction so the caller logs the store's own count.

**Optional extra.** ``psycopg`` is imported when a store is constructed, not when this module is imported, so
``import auto_inform`` works without the extra. Constructing one without it raises ``ConfigurationError``
naming the extra — the same shape as the S3 writer.

**Thread affinity** is a port requirement (``docs/04`` §2.4) and this store enforces it rather than relying
on psycopg's own concurrency rules: every method must be called from the thread that opened the store.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

from auto_inform.application.ports import Clock, FailureRecord, SaveResult
from auto_inform.domain.errors import ConfigurationError, StoreError
from auto_inform.domain.event import Event, EventStatus
from auto_inform.infrastructure.clock import SystemClock
from auto_inform.infrastructure.settings import Settings

if TYPE_CHECKING:
    import psycopg

#: Bound the two waits a scheduled job must never hang on: connecting, and a statement blocked by another
#: transaction. The processing runner has no per-report deadline (ADR-0030), so an unbounded store call is
#: a wedged job. Passed as keywords, never concatenated onto the DSN -- that would recreate the escaping
#: problem the redaction below guards against.
_CONNECT_TIMEOUT_SECONDS = 10
_STATEMENT_TIMEOUT_MS = 30_000
_LOCK_TIMEOUT_MS = 5_000

#: Password-shaped text in driver messages: URI userinfo and keyword-form ``password=`` values.
_URI_USERINFO = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")
_KEYWORD_PASSWORD = re.compile(r"(password\s*=\s*)(?:'[^']*'|\S+)", re.IGNORECASE)


def _redact(text: str, dsn: str) -> str:
    """Remove anything password-shaped before driver text leaves the process."""
    redacted = text.replace(dsn, "***") if dsn else text
    redacted = _URI_USERINFO.sub(r"\1***\3", redacted)
    return _KEYWORD_PASSWORD.sub(r"\1***", redacted)


#: Applied idempotently on open. Same columns and index as SQLite (``docs/04`` §3), with the types that
#: backend provides natively.
_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS events (
        event_type    TEXT        NOT NULL,
        event_id      TEXT        NOT NULL,
        payload       JSONB       NOT NULL,
        discovered_at TIMESTAMPTZ NOT NULL,
        source        TEXT        NOT NULL,
        status        TEXT        NOT NULL,
        attempts      INTEGER     NOT NULL DEFAULT 0,
        last_error    TEXT,
        output_uri    TEXT,
        processed_at  TIMESTAMPTZ,
        PRIMARY KEY (event_type, event_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS events_pending_idx
        ON events (status, event_type, discovered_at)
    """,
)


def _psycopg() -> Any:
    """Import psycopg on demand, with an error that names the extra."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigurationError(
            "the PostgreSQL event store requires the 'postgres' extra: "
            "uv add 'auto_inform[postgres]'"
        ) from exc
    return psycopg


def _aware(value: datetime) -> datetime:
    """Normalise to UTC, rejecting a naive timestamp rather than guessing its offset."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"timestamp {value!r} is naive; events carry timezone-aware UTC timestamps"
        )
    return value.astimezone(UTC)


class PostgresEventStore:
    """A shared PostgreSQL database holding the queue. Use it as a context manager."""

    def __init__(
        self,
        dsn: str,
        *,
        max_attempts: int = 3,
        clock: Clock | None = None,
    ) -> None:
        """Connect to ``dsn`` and apply the idempotent schema."""
        if max_attempts < 1:
            raise ConfigurationError(f"max_attempts is {max_attempts!r}; it must be at least 1")
        if not dsn:
            raise ConfigurationError(
                "a PostgreSQL DSN is required; set AUTO_INFORM_POSTGRES_DSN (docs/07 §1.1)"
            )

        self._dsn = dsn
        self._max_attempts = max_attempts
        self._clock = clock if clock is not None else SystemClock()
        self._owner = threading.get_ident()
        self._connection: psycopg.Connection[dict[str, Any]] | None = None
        self._open()

    @classmethod
    def from_settings(cls, settings: Settings, *, clock: Clock | None = None) -> PostgresEventStore:
        """Build the store the settings ask for, refusing a configuration that names the other backend."""
        if settings.event_store != "postgres":
            raise ConfigurationError(
                f"AUTO_INFORM_EVENT_STORE={settings.event_store} is not 'postgres'; "
                "use this store only when AUTO_INFORM_EVENT_STORE=postgres"
            )
        if not settings.postgres_dsn:
            raise ConfigurationError(
                "AUTO_INFORM_POSTGRES_DSN is required when AUTO_INFORM_EVENT_STORE=postgres"
            )
        return cls(
            settings.postgres_dsn,
            max_attempts=settings.event_max_attempts,
            clock=clock,
        )

    def __repr__(self) -> str:
        """Never print the DSN: it carries the password."""
        return f"PostgresEventStore(dsn=***, max_attempts={self._max_attempts})"

    # --- the port -------------------------------------------------------------

    def save(self, events: Iterable[Event]) -> SaveResult:
        """Insert events, leaving existing rows untouched and rejecting a foreign source's key."""
        new = 0
        duplicates = 0
        with self._transaction():
            for event in events:
                cursor = self._execute(
                    "INSERT INTO events "
                    "(event_type, event_id, payload, discovered_at, source, status) "
                    "VALUES (%s, %s, %s::jsonb, %s, %s, %s) "
                    "ON CONFLICT (event_type, event_id) DO NOTHING",
                    (
                        event.event_type,
                        event.event_id,
                        json.dumps(
                            dict(event.payload),
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        _aware(event.discovered_at),
                        event.source,
                        EventStatus.NEW.value,
                    ),
                )
                if cursor.rowcount == 1:
                    new += 1
                    continue

                # As in SQLite: a duplicate from a *different* source is a contract violation, not a
                # duplicate, and must not silently suppress the second feed's event (docs/04 §2.1).
                row = self._execute(
                    "SELECT source FROM events WHERE event_type = %s AND event_id = %s",
                    (event.event_type, event.event_id),
                ).fetchone()
                if row is not None and row["source"] != event.source:
                    raise StoreError(
                        f"{event.event_type}/{event.event_id} is already stored by source "
                        f"{row['source']!r}, not {event.source!r}. An event_id must be unique within "
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
        """``NEW`` events under the attempt limit, oldest first, optionally filtered and capped."""
        if limit is not None and limit < 0:
            raise ValueError(f"limit must not be negative; got {limit!r}")

        sql = [
            "SELECT event_type, event_id, payload, discovered_at, source FROM events",
            "WHERE status = %s AND attempts < %s",
        ]
        parameters: list[object] = [EventStatus.NEW.value, self._max_attempts]
        if event_type is not None:
            sql.append("AND event_type = %s")
            parameters.append(event_type)
        sql.append("ORDER BY discovered_at, event_type, event_id")
        if limit is not None:
            sql.append("LIMIT %s")
            parameters.append(limit)

        # A read commits its (empty) transaction too: leaving it open holds a snapshot and an
        # AccessShareLock until the next write, and a failed read would leave the session aborted, so the
        # *next* call would report a second, misleading failure.
        with self._transaction():
            cursor = self._execute(" ".join(sql), tuple(parameters))
            rows = cursor.fetchall()
        return [self._event_from_row(row) for row in rows]

    def mark_processed(self, event: Event, *, output_uri: str) -> None:
        """Transition a ``NEW`` event to ``PROCESSED``, clearing any previous error."""
        with self._transaction():
            cursor = self._execute(
                "UPDATE events "
                "SET status = %s, processed_at = %s, output_uri = %s, last_error = NULL "
                "WHERE event_type = %s AND event_id = %s AND status = %s",
                (
                    EventStatus.PROCESSED.value,
                    _aware(self._clock.now()),
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
        """Count one failed attempt, retiring the event at ``max_attempts`` (ADR-0019)."""
        with self._transaction():
            cursor = self._execute(
                "UPDATE events "
                "SET attempts = attempts + 1, "
                "    status = CASE WHEN attempts + 1 >= %s THEN %s ELSE %s END, "
                "    last_error = %s "
                "WHERE event_type = %s AND event_id = %s AND status = %s",
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
                "SELECT attempts, status FROM events WHERE event_type = %s AND event_id = %s",
                (event.event_type, event.event_id),
            ).fetchone()

        if row is None:  # pragma: no cover - the UPDATE above matched this row in this transaction
            raise StoreError(
                f"event {event.event_type}/{event.event_id} disappeared during mark_failed"
            )
        return FailureRecord(
            attempts=int(row["attempts"]),
            retired=row["status"] == EventStatus.FAILED.value,
        )

    def close(self) -> None:
        """Release the connection. Idempotent, and safe after an exception."""
        connection = self._connection
        if connection is None:
            return
        try:
            connection.close()
        except Exception as exc:
            if isinstance(exc, _psycopg().Error):
                raise StoreError(
                    f"could not close the event store: {_redact(str(exc), self._dsn)}"
                ) from exc
            raise
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
            # The body's failure is the diagnosis; a close that fails after it is a second symptom.
            pass

    # --- internals ------------------------------------------------------------

    def _open(self) -> None:
        psycopg = _psycopg()
        from psycopg.rows import dict_row

        try:
            connection = psycopg.connect(
                self._dsn,
                row_factory=dict_row,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            )
        except psycopg.ProgrammingError as exc:
            # A malformed DSN is the one connect failure whose message can quote part of the password:
            # libpq reports the token it could not parse (a password with a space leaks the fragment after
            # it). Keep the diagnosis generic and drop the cause, so neither the message nor the logged
            # traceback can carry it.
            raise StoreError(
                "could not connect to the event store: the DSN is malformed "
                f"({type(exc).__name__}); check AUTO_INFORM_POSTGRES_DSN"
            ) from None
        except psycopg.Error as exc:
            raise StoreError(
                "could not connect to the event store: "
                f"{type(exc).__name__}: {_redact(str(exc), self._dsn)}"
            ) from exc

        try:
            # The statement and lock timeouts are set with `set_config`, not passed as `options=` on the
            # connection: a keyword option *replaces* any `options` the caller put in the DSN, which would
            # silently drop their `search_path` (or timezone) and point the store at the wrong schema.
            connection.execute(
                "SELECT set_config('statement_timeout', %s, false), "
                "set_config('lock_timeout', %s, false)",
                (str(_STATEMENT_TIMEOUT_MS), str(_LOCK_TIMEOUT_MS)),
            )
            # Not `with connection:` -- psycopg3's connection context manager *closes* the connection on
            # exit, so the store would hold a closed handle. Commit explicitly instead.
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.commit()
        except psycopg.Error as exc:
            connection.close()
            raise StoreError(
                f"could not initialise the event store: {_redact(str(exc), self._dsn)}"
            ) from exc

        self._connection = connection

    def _require_open(self) -> psycopg.Connection[dict[str, Any]]:
        if threading.get_ident() != self._owner:
            raise StoreError(
                "the PostgreSQL event store is thread-affine: use it from the thread that opened it "
                "(docs/04 §2.4)"
            )
        if self._connection is None:
            raise StoreError("the event store is closed; construct one per job run (docs/04 §2.5)")
        return self._connection

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Commit on success, roll back on failure, translating both into ``StoreError``."""
        connection = self._require_open()
        try:
            yield
        except BaseException:
            # A rollback that fails must not mask the failure that caused it.
            with suppress(Exception):
                connection.rollback()
            raise
        else:
            try:
                connection.commit()
            except Exception as exc:
                with suppress(Exception):
                    connection.rollback()
                if isinstance(exc, _psycopg().Error):
                    raise StoreError(f"event store error: {_redact(str(exc), self._dsn)}") from exc
                raise

    def _execute(self, sql: str, parameters: tuple[object, ...] = ()) -> Any:
        """Run one statement, translating psycopg's errors into the framework's vocabulary."""
        connection = self._require_open()
        try:
            return connection.execute(sql, parameters)
        except Exception as exc:
            if isinstance(exc, _psycopg().Error):
                # Redacted like every other driver message, and with the cause dropped: a traceback line
                # can quote the same text, which is the leak the malformed-DSN path also guards against.
                raise StoreError(
                    f"event store error ({type(exc).__name__}): {_redact(str(exc), self._dsn)}"
                ) from None
            raise

    @staticmethod
    def _event_from_row(row: dict[str, Any]) -> Event:
        return Event(
            event_type=row["event_type"],
            event_id=row["event_id"],
            payload=row["payload"],
            # TIMESTAMPTZ decodes in the *session* timezone, which is not necessarily UTC. The SQLite
            # store normalises on read for the same reason: `Event.discovered_at` is documented as
            # timezone-aware UTC, and a report must not print the host's offset instead of `Z`.
            discovered_at=row["discovered_at"].astimezone(UTC),
            source=row["source"],
        )

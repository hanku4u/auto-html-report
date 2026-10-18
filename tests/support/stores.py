"""An in-memory ``EventStore`` with the schema's semantics.

``docs/09`` names this double as the one that drives the ``run_*`` tests, and ``docs/04`` says the store
behaviour suite runs against every implementation. So it is not a mock with canned answers: it implements
the port's rules -- insert-only ``save``, cross-source collision rejection, oldest-first ``pending`` with
retirement, single-direction lifecycle transitions -- and the shared suite runs against it. A divergence
from SQLite fails a test rather than a demo.

Like every store, it is **thread-affine**: it records the constructing thread and refuses use from
another, so a runner that touches the store off the main thread fails here exactly as it would with the
real backend (``docs/04`` §2.4).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from auto_inform.application.ports import Clock, FailureRecord, SaveResult
from auto_inform.domain.errors import ConfigurationError, StoreError
from auto_inform.domain.event import Event, EventStatus
from auto_inform.infrastructure.clock import SystemClock


@dataclass(slots=True)
class _Row:
    """One stored event, mirroring the columns that carry semantics."""

    event: Event
    status: EventStatus = EventStatus.NEW
    attempts: int = 0
    last_error: str | None = None
    output_uri: str | None = None
    processed_at: datetime | None = None


class FakeEventStore:
    """See the module docstring. Use it as a context manager, as the job scripts do."""

    def __init__(self, *, max_attempts: int = 3, clock: Clock | None = None) -> None:
        if max_attempts < 1:
            raise ConfigurationError(f"max_attempts is {max_attempts!r}; it must be at least 1")
        self._rows: dict[tuple[str, str], _Row] = {}
        self._max_attempts = max_attempts
        self._clock = clock if clock is not None else SystemClock()
        self._owner = threading.get_ident()
        self._closed = False

    # --- the port -------------------------------------------------------------

    def save(self, events: Iterable[Event]) -> SaveResult:
        """Insert events, leaving existing rows untouched and rejecting a foreign source's key."""
        self._check_usable()
        claimed: dict[tuple[str, str], Event] = {}
        duplicates = 0

        # Validate and count before mutating, so a collision leaves the store untouched -- the same
        # all-or-nothing result the SQLite transaction produces.
        for event in events:
            self._check_persistable(event)
            key = (event.event_type, event.event_id)
            stored = self._rows.get(key)
            if stored is not None:
                self._check_source(key, stored.event.source, event.source)
                duplicates += 1
                continue
            earlier = claimed.get(key)
            if earlier is not None:
                self._check_source(key, earlier.source, event.source)
                duplicates += 1
                continue
            claimed[key] = event

        for key, event in claimed.items():
            self._rows[key] = _Row(event=event)
        return SaveResult(new=len(claimed), duplicates=duplicates)

    def pending(
        self,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        self._check_usable()
        if limit is not None and limit < 0:
            raise ValueError(f"limit must not be negative; got {limit!r}")

        rows = [
            row
            for row in self._rows.values()
            if row.status is EventStatus.NEW
            and row.attempts < self._max_attempts
            and (event_type is None or row.event.event_type == event_type)
        ]
        rows.sort(
            key=lambda row: (
                row.event.discovered_at,
                row.event.event_type,
                row.event.event_id,
            )
        )
        events = [row.event for row in rows]
        return events if limit is None else events[:limit]

    def mark_processed(self, event: Event, *, output_uri: str) -> None:
        self._check_usable()
        row = self._require_new(event, "mark")
        row.status = EventStatus.PROCESSED
        row.processed_at = self._clock.now()
        row.output_uri = output_uri
        row.last_error = None

    def mark_failed(self, event: Event, *, reason: str) -> FailureRecord:
        self._check_usable()
        row = self._require_new(event, "record a failure for")
        row.attempts += 1
        row.last_error = reason
        if row.attempts >= self._max_attempts:
            row.status = EventStatus.FAILED
        return FailureRecord(
            attempts=row.attempts,
            retired=row.status is EventStatus.FAILED,
        )

    def close(self) -> None:
        """Idempotent, like the real stores."""
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- internals ------------------------------------------------------------

    def _check_usable(self) -> None:
        if self._closed:
            raise StoreError("the fake event store is closed")
        if threading.get_ident() != self._owner:
            raise StoreError(
                "the fake event store is thread-affine: use it from the thread that created it "
                "(docs/04 §2.4)"
            )

    @staticmethod
    def _check_persistable(event: Event) -> None:
        """Reject what the SQLite store rejects, so the double cannot be more forgiving than production.

        The store suite treats this fake as a real implementation; a naive timestamp or a payload that
        cannot be serialised must fail here too, or a runner test would pass against the fake and fail in
        a job.
        """
        if event.discovered_at.tzinfo is None or event.discovered_at.utcoffset() is None:
            raise ValueError(
                f"timestamp {event.discovered_at!r} is naive; events carry timezone-aware UTC timestamps"
            )
        try:
            json.dumps(dict(event.payload))
        except (TypeError, ValueError) as exc:
            raise TypeError(f"payload is not JSON-serialisable: {exc}") from exc

    def _check_source(self, key: tuple[str, str], stored: str, incoming: str) -> None:
        if stored != incoming:
            raise StoreError(
                f"{key[0]}/{key[1]} is already stored by source {stored!r}, not {incoming!r}. "
                "An event_id must be unique within its event_type, so two sources cannot reuse one "
                "(docs/04 §2.1)."
            )

    def _require_new(self, event: Event, action: str) -> _Row:
        row = self._rows.get((event.event_type, event.event_id))
        if row is None or row.status is not EventStatus.NEW:
            raise StoreError(
                f"cannot {action} {event.event_type}/{event.event_id}: it is not a NEW event in this "
                "store (already processed, retired, or never saved)"
            )
        return row

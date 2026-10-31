"""Discovery: run every discoverer concurrently, persist what they found.

The two jobs in ``docs/05-discovery-and-processing.md`` share only the event store. Discovery's half is
deliberately small: a discoverer yields events, the run saves them, and the store's insert-only
``save()`` makes the whole thing idempotent, so a 15-minute schedule against a rolling API window
reports the same events as duplicates instead of doing the work again.

**One broken source must not cost the others their events.** A discoverer that raises is recorded in
its :class:`DiscoveryOutcome` and never re-raised; the remaining discoverers still run and still
persist. That isolation, the per-attempt budget and the retry policy are not implemented here -- they
are ``application/execution.py``, shared with blocks (``docs/05`` §1).

**Persisting happens on the calling thread.** Workers only collect events; ``save()`` runs here, after
the concurrent phase, because every ``EventStore`` is thread-affine by contract (``docs/04`` §2.4).
That also means a store failure aborts the run rather than being recorded per discoverer: an
unreachable store is an operator problem, not one source's bad day.

**The declared type is enforced.** ``Discoverer.event_type`` is the type a discoverer owns, and an
event of a different type is refused rather than saved: storing it would put a row in a queue whose
report factory expects another shape, and the failure would surface later, in processing, with no
trace back to the discoverer that caused it.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final

from auto_html_report.application.context import DiscoveryContext
from auto_html_report.application.execution import (
    DISCOVERY_EXECUTION,
    ExecutionResult,
    ExecutionStatus,
    Task,
    execute_tasks,
)
from auto_html_report.application.ports import EventStore
from auto_html_report.domain.errors import ConfigurationError, PermanentBlockError
from auto_html_report.domain.event import Event

#: A discoverer yielding more than this in one run is treated as broken rather than buffered. The
#: framework bounds *time* -- per-attempt budgets and the hang guard -- but not *memory*, and the hang
#: guard only stops waiting for a wedged worker; it cannot stop the worker. A hostile or broken upstream
#: returning an endless generator would therefore grow that worker's buffer for as long as the process
#: lives. Ten thousand is two orders of magnitude above the documented operating rate (~200 events/day,
#: ``docs/04`` §5), so a legitimate source never meets it.
MAX_EVENTS_PER_DISCOVERER: Final = 10_000


class Discoverer(ABC):
    """Produces events of exactly one type from one external source.

    ``discover()`` may perform I/O and may be called more than once per run when a failure is
    retryable, so it must be safe to repeat. It is called on a worker thread; anything it holds that
    is not thread-safe needs protecting, exactly as a block's ``collect()`` does.
    """

    #: The one type this discoverer produces. It fills the store's conflict key together with
    #: ``event_id``, which is why it must be stable across runs and deployments.
    event_type: ClassVar[str]

    @abstractmethod
    def discover(self, context: DiscoveryContext) -> Iterable[Event]:
        """Return the events currently visible at the source. May perform I/O."""


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    """What one discoverer did, including how it failed if it did.

    ``error_type`` and ``error_message`` are set only when the discoverer (or its contract check)
    failed. ``discovered`` counts what it yielded, and ``new`` / ``duplicates`` come from the store, so
    a re-run over an unchanged source shows ``discovered == duplicates`` and ``new == 0``.
    """

    discoverer: str
    event_type: str
    discovered: int
    new: int
    duplicates: int
    duration_seconds: float
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    """One discovery job run: one outcome per discoverer, in the order they were passed."""

    run_id: str
    outcomes: tuple[DiscoveryOutcome, ...]
    started_at: datetime
    finished_at: datetime

    @property
    def all_succeeded(self) -> bool:
        """Whether every discoverer ran without error. Persistence is not part of this question."""
        return all(outcome.error_type is None for outcome in self.outcomes)


def run_discovery(
    discoverers: Sequence[Discoverer],
    *,
    store: EventStore,
    context: DiscoveryContext,
) -> DiscoveryRun:
    """Run every discoverer concurrently; persist what they found.

    A discoverer that raises is recorded in its :class:`DiscoveryOutcome`, not re-raised: one broken
    source must not prevent the others from being persisted. Raises
    :class:`~auto_html_report.domain.errors.ConfigurationError` for an invalid discoverer set and
    :class:`~auto_html_report.domain.errors.StoreError` when the store itself fails, because those are
    operator problems rather than one source's data condition.
    """
    _validate(discoverers)

    results = execute_tasks(
        [_task(discoverer, context) for discoverer in discoverers],
        settings=context.settings,
        clock=context.clock,
        logger=context.logger,
        run_id=context.run.run_id,
        vocabulary=DISCOVERY_EXECUTION,
    )
    outcomes = tuple(
        _persist(discoverer, result, store=store, context=context)
        for discoverer, result in zip(discoverers, results, strict=True)
    )
    return DiscoveryRun(
        run_id=context.run.run_id,
        outcomes=outcomes,
        started_at=context.run.started_at,
        finished_at=context.clock.now(),
    )


def _task(
    discoverer: Discoverer, context: DiscoveryContext
) -> Task[tuple[Event, ...], tuple[Event, ...]]:
    """One discoverer as a task: drain its iterable and check what it declared.

    Draining inside the worker matters: ``discover()`` is usually a generator, so the I/O happens
    during iteration. Materialising it here means that work is inside the per-attempt budget and the
    retry loop, rather than in the main thread's ``save()`` call where a hung generator would sit
    outside every guard.
    """
    name = type(discoverer).__name__

    def attempt(_attempt_number: int) -> tuple[Event, ...]:
        events = _drain(discoverer, context, name)
        _check_yielded_events(events, discoverer, name)
        return events

    # No finalise step: persisting is the main thread's job, because the store is thread-affine.
    return Task(name=name, attempt=attempt)


def _drain(discoverer: Discoverer, context: DiscoveryContext, name: str) -> tuple[Event, ...]:
    """Materialise ``discover()`` with a ceiling, because an iterable from an API has no natural end.

    Raising ``PermanentBlockError`` rather than a generic error marks it non-retryable: a source that
    exceeds the ceiling will do so again on the next attempt.
    """
    events: list[Event] = []
    for event in discoverer.discover(context):
        if len(events) >= MAX_EVENTS_PER_DISCOVERER:
            raise PermanentBlockError(
                f"{name}.discover() yielded more than {MAX_EVENTS_PER_DISCOVERER} events in one run; "
                "that is a broken source, not a batch (docs/05 §1)"
            )
        events.append(event)
    return tuple(events)


def _check_yielded_events(events: tuple[Event, ...], discoverer: Discoverer, name: str) -> None:
    """Refuse anything the store would choke on, while it is still *this* discoverer's problem.

    Raised inside the attempt, so it is recorded as that discoverer's failure and the others proceed.
    ``ConfigurationError`` is not in the retryable set, so it costs exactly one attempt.

    The timestamp and payload checks are the point of the whole function. ``save()`` would raise a plain
    ``ValueError``/``TypeError`` for a naive ``discovered_at`` or a non-JSON payload, on the calling
    thread, after the concurrent phase -- aborting the run and losing every other source's events, which
    is exactly what ``docs/05`` §1 forbids. Validating here keeps one bad source's data inside that
    source's outcome.
    """
    expected = discoverer.event_type
    for event in events:
        if not isinstance(event, Event):
            raise ConfigurationError(
                f"{name}.discover() yielded {type(event).__name__}, not an Event. "
                "Discoverers yield Event instances (docs/05 §1)."
            )
        if event.event_type != expected:
            raise ConfigurationError(
                f"{name}.discover() declares event_type={expected!r} but yielded an event of type "
                f"{event.event_type!r}. A discoverer owns exactly one event type (docs/05 §1)."
            )
        # Every field the schema declares NOT NULL is checked here, not just the interesting ones. A
        # NULL source or event_id would otherwise reach ``save()`` and raise an integrity error on the
        # calling thread, aborting the run and losing the *other* sources' events -- the failure this
        # function exists to prevent, arriving through the field nobody thought to check.
        if not isinstance(event.event_id, str) or not event.event_id:
            raise ConfigurationError(
                f"{name}.discover() yielded an event with event_id={event.event_id!r}; it must be a "
                "non-empty string, because it is half of the store's conflict key (docs/04 §2.1)."
            )
        if not isinstance(event.source, str) or not event.source:
            raise ConfigurationError(
                f"{name}.discover() yielded an event with source={event.source!r}; it must be a "
                "non-empty string naming the feed that produced it (docs/04 §1)."
            )
        if not isinstance(event.discovered_at, datetime):
            raise ConfigurationError(
                f"{name}.discover() yielded an event whose discovered_at is "
                f"{type(event.discovered_at).__name__}, not a datetime"
            )
        if event.discovered_at.tzinfo is None or event.discovered_at.utcoffset() is None:
            raise ConfigurationError(
                f"{name}.discover() yielded an event with a naive discovered_at "
                f"({event.discovered_at!r}); events carry timezone-aware UTC timestamps (docs/04 §1)."
            )
        if not isinstance(event.payload, Mapping):
            raise ConfigurationError(
                f"{name}.discover() yielded an event whose payload is "
                f"{type(event.payload).__name__}, not a mapping (docs/04 §1)"
            )
        try:
            # allow_nan=False: SQLite's TEXT column would happily store `NaN`, while PostgreSQL's
            # jsonb rejects it, so the same event would succeed on one backend and abort the run on the
            # other. A non-finite number is a data error and belongs to the source that produced it.
            json.dumps(dict(event.payload), allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ConfigurationError(
                f"{name}.discover() yielded an event whose payload is not JSON-serialisable: {exc}"
            ) from exc


def _persist(
    discoverer: Discoverer,
    result: ExecutionResult[tuple[Event, ...]],
    *,
    store: EventStore,
    context: DiscoveryContext,
) -> DiscoveryOutcome:
    """Save one discoverer's events, or record why it produced none.

    ``discovery.finish`` is logged only on the success path: its documented fields are what was
    persisted, and a failed discoverer has already logged ``discovery.failed`` with the error.
    """
    name = type(discoverer).__name__
    if result.status is not ExecutionStatus.OK or result.value is None:
        return DiscoveryOutcome(
            discoverer=name,
            event_type=discoverer.event_type,
            discovered=0,
            new=0,
            duplicates=0,
            duration_seconds=result.duration_seconds,
            error_type=result.error_type,
            error_message=result.error_message,
        )

    events = result.value
    saved = store.save(events)
    outcome = DiscoveryOutcome(
        discoverer=name,
        event_type=discoverer.event_type,
        discovered=len(events),
        new=saved.new,
        duplicates=saved.duplicates,
        duration_seconds=result.duration_seconds,
    )
    context.logger.info(
        "discovery.finish",
        extra={
            "run_id": context.run.run_id,
            "discoverer": name,
            "event_type": discoverer.event_type,
            "new": saved.new,
            "duplicates": saved.duplicates,
            "duration_ms": round(outcome.duration_seconds * 1000, 1),
        },
    )
    return outcome


def _validate(discoverers: Sequence[Discoverer]) -> None:
    """Reject a run that could not persist anything sensible."""
    if not discoverers:
        raise ConfigurationError("a discovery run must contain at least one discoverer")

    counts: dict[str, int] = {}
    for discoverer in discoverers:
        name = type(discoverer).__name__
        counts[name] = counts.get(name, 0) + 1
    repeated = sorted(name for name, count in counts.items() if count > 1)
    if repeated:
        # Outcomes and log lines identify a source by class name (docs/05 §1), so two instances of one
        # class would be indistinguishable in the run and in the log -- the exact problem the
        # `discoverer` field was added to solve. A distinct source needs its own Discoverer subclass.
        raise ConfigurationError(
            f"two discoverers of the same class: {repeated}. Outcomes are identified by class name, "
            "so a second instance would be indistinguishable; give each source its own Discoverer."
        )

    for discoverer in discoverers:
        name = type(discoverer).__name__
        event_type: Any = getattr(discoverer, "event_type", None)
        if not isinstance(event_type, str) or not event_type:
            # An empty type would still "work" -- the store would happily save rows under "" -- and
            # every one of those rows would be invisible to a factory filtering by a real type.
            raise ConfigurationError(
                f"{name}.event_type is {event_type!r}; it must be a non-empty string"
            )

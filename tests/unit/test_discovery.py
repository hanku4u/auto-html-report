"""Discovery: persistence, idempotence, isolation, and the rules shared with blocks.

The done-when for stage 8 is acceptance criterion 12: ``run_discovery`` persists events, and an
identical second run reports them as duplicates. Everything else here exists because the first thing
that goes wrong in production is one broken source, and the second is a discoverer that quietly
persists events of the wrong type.

Timing runs against the ``FakeClock`` throughout: a retry backoff is asserted from the recorded sleeps,
and a timeout costs microseconds rather than the configured budget.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import pytest

from auto_html_report.application.discovery import Discoverer, run_discovery
from auto_html_report.application.ports import EventStore, FailureRecord, SaveResult
from auto_html_report.domain.errors import (
    ConfigurationError,
    PermanentBlockError,
    StoreError,
    TransientBlockError,
)
from auto_html_report.domain.event import Event
from auto_html_report.infrastructure.settings import Settings
from auto_html_report.infrastructure.stores.sqlite_store import SqliteEventStore
from tests.support.builders import make_event
from tests.support.clocks import DEFAULT_START, FakeClock
from tests.support.contexts import make_discovery_context

START = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _event_with(**overrides: Any) -> Event:
    """An event whose defaults are valid, with named fields overridden for a contract test."""
    fields: dict[str, Any] = {
        "event_type": "WAFER_FAILURE",
        "event_id": "x",
        "payload": {},
        "discovered_at": datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
        "source": "src",
    }
    fields.update(overrides)
    return Event(**fields)


# --- discoverers that exercise one behaviour each ------------------------------


class _YieldingDiscoverer(Discoverer):
    """Yields one event per lot, and records the thread it ran on."""

    event_type = "WAFER_FAILURE"

    def __init__(self, *lots: str) -> None:
        self.lots = lots
        self.threads: list[int] = []

    def discover(self, context: Any) -> Iterable[Event]:
        self.threads.append(threading.get_ident())
        for lot in self.lots:
            yield make_event(lot, event_type=self.event_type, payload={"lot": lot})


class _AlphaDiscoverer(_YieldingDiscoverer):
    """A distinct source class, for asserting outcome identity rather than a symmetric list."""

    event_type = "WAFER_FAILURE"


class _BetaDiscoverer(_YieldingDiscoverer):
    event_type = "MAINTENANCE"


class _FailingDiscoverer(Discoverer):
    event_type = "WAFER_FAILURE"

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def discover(self, context: Any) -> Iterable[Event]:
        self.calls += 1
        raise self.error


class _FlakyDiscoverer(Discoverer):
    """Fails transiently once, then succeeds, so the retry path is exercised end to end."""

    event_type = "WAFER_FAILURE"
    calls = 0

    def discover(self, context: Any) -> Iterable[Event]:
        self.calls += 1
        if self.calls == 1:
            raise TransientBlockError("upstream 503")
        yield make_event("late", event_type=self.event_type)


class _WrongTypeDiscoverer(Discoverer):
    event_type = "WAFER_FAILURE"

    def discover(self, context: Any) -> Iterable[Event]:
        yield make_event("x", event_type="MAINTENANCE")


class _NotAnEventDiscoverer(Discoverer):
    event_type = "WAFER_FAILURE"

    def discover(self, context: Any) -> Iterable[Event]:
        yield {"event_id": "x"}  # type: ignore[misc]


class _AdvancingDiscoverer(Discoverer):
    """Simulates slow upstream I/O by moving the fake clock past the budget."""

    event_type = "MAINTENANCE"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def discover(self, context: Any) -> Iterable[Event]:
        context.clock.advance(self.seconds)
        yield make_event("slow", event_type=self.event_type)


class _RaisingStore:
    """A store wrapper that records the persisting thread and can be told to fail."""

    def __init__(self, inner: EventStore, *, fail_with: BaseException | None = None) -> None:
        self._inner = inner
        self._fail_with = fail_with
        self.save_threads: list[int] = []

    def save(self, events: Iterable[Event]) -> SaveResult:
        self.save_threads.append(threading.get_ident())
        if self._fail_with is not None:
            raise self._fail_with
        return self._inner.save(events)

    def pending(self, *, event_type: str | None = None, limit: int | None = None) -> list[Event]:
        return self._inner.pending(event_type=event_type, limit=limit)

    def mark_processed(self, event: Event, *, output_uri: str) -> None:
        self._inner.mark_processed(event, output_uri=output_uri)

    def mark_failed(self, event: Event, *, reason: str) -> FailureRecord:
        return self._inner.mark_failed(event, reason=reason)

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteEventStore]:
    with SqliteEventStore(tmp_path / "events.sqlite3") as opened:
        yield opened


# --- persistence and idempotence ----------------------------------------------


def test_run_discovery_should_persist_the_events_a_discoverer_yields(
    store: SqliteEventStore,
) -> None:
    # given a discoverer that finds two lots
    discoverer = _YieldingDiscoverer("L1", "L2")

    # when discovery runs
    run = run_discovery([discoverer], store=store, context=make_discovery_context())

    # then both are stored as new, and the outcome reports what happened
    outcome = run.outcomes[0]
    assert (outcome.discovered, outcome.new, outcome.duplicates) == (2, 2, 0)
    assert sorted(event.event_id for event in store.pending()) == ["L1", "L2"]
    assert run.all_succeeded


def test_an_identical_second_run_should_report_duplicates_not_new(
    store: SqliteEventStore,
) -> None:
    # given a source whose rolling window has already been persisted
    discoverer = _YieldingDiscoverer("L1", "L2")
    run_discovery([discoverer], store=store, context=make_discovery_context())

    # when the same window is discovered again
    second = run_discovery([discoverer], store=store, context=make_discovery_context())

    # then nothing is new and nothing is doubled: insert-only save is what makes the schedule safe
    # (acceptance criterion 12)
    outcome = second.outcomes[0]
    assert (outcome.discovered, outcome.new, outcome.duplicates) == (2, 0, 2)
    assert len(store.pending()) == 2


def test_redicovery_should_not_reset_an_event_that_was_already_processed(
    store: SqliteEventStore,
) -> None:
    # given an event whose report has been delivered
    discoverer = _YieldingDiscoverer("L1")
    first = run_discovery([discoverer], store=store, context=make_discovery_context())
    (event,) = store.pending()
    assert first.outcomes[0].new == 1
    store.mark_processed(event, output_uri="file:///reports/L1.html")

    # when discovery sees it again
    run_discovery([discoverer], store=store, context=make_discovery_context())

    # then it is not put back in the queue
    assert store.pending() == []


# --- isolation and ordering ----------------------------------------------------


def test_a_failing_discoverer_should_not_stop_the_others_from_persisting(
    store: SqliteEventStore,
) -> None:
    # given one broken source and one healthy one
    healthy = _YieldingDiscoverer("L1")
    broken = _FailingDiscoverer(PermanentBlockError("yield API is down"))

    # when discovery runs
    run = run_discovery([broken, healthy], store=store, context=make_discovery_context())

    # then the failure is recorded in the outcome, not raised, and the healthy source still persisted
    assert run.outcomes[0].error_type == "PermanentBlockError"
    assert run.outcomes[0].error_message == "yield API is down"
    assert (run.outcomes[1].new, run.outcomes[1].duplicates) == (1, 0)
    assert [event.event_id for event in store.pending()] == ["L1"]
    assert not run.all_succeeded


def test_outcomes_should_preserve_the_order_the_discoverers_were_passed(
    store: SqliteEventStore,
) -> None:
    # given two discoverers of different classes and event types. Finish order is not controllable
    # without flakiness, so the contract is asserted directly -- and with distinct identities, because a
    # list of two identical names would pass whether or not the outcomes were reversed.
    run = run_discovery(
        [_AlphaDiscoverer("A"), _BetaDiscoverer("B")],
        store=store,
        context=make_discovery_context(),
    )

    # then outcomes follow the argument order, and each carries its own source's identity
    assert [outcome.discoverer for outcome in run.outcomes] == [
        "_AlphaDiscoverer",
        "_BetaDiscoverer",
    ]
    assert [outcome.event_type for outcome in run.outcomes] == ["WAFER_FAILURE", "MAINTENANCE"]
    assert [outcome.new for outcome in run.outcomes] == [1, 1]
    assert {event.event_id for event in store.pending()} == {"A", "B"}


def test_a_generator_that_raises_mid_iteration_should_be_that_discoverers_failure(
    store: SqliteEventStore,
) -> None:
    # given a discoverer whose generator yields once and then blows up, as a partial API response does
    class _PartialDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            yield make_event("first")
            raise PermanentBlockError("stream cut off")

    # when discovery runs
    run = run_discovery([_PartialDiscoverer()], store=store, context=make_discovery_context())

    # then it is a failure, and nothing from the partial batch was persisted: the events are materialised
    # inside the worker, so a broken stream cannot half-save a source
    assert run.outcomes[0].error_type == "PermanentBlockError"
    assert store.pending() == []


def test_a_store_failure_should_abort_the_run(store: SqliteEventStore) -> None:
    # given a store that cannot write
    failing = _RaisingStore(store, fail_with=StoreError("disk is full"))

    # when discovery runs
    # then it propagates: an unreachable store is an operator problem, not one source's bad day
    with pytest.raises(StoreError, match="disk is full"):
        run_discovery([_YieldingDiscoverer("L1")], store=failing, context=make_discovery_context())


def test_persisting_should_happen_on_the_calling_thread(store: SqliteEventStore) -> None:
    # given a store recording which thread calls save, and a discoverer recording its own thread
    spy = _RaisingStore(store)
    discoverer = _YieldingDiscoverer("L1")
    main_thread = threading.get_ident()

    # when discovery runs
    with spy:
        run_discovery([discoverer], store=spy, context=make_discovery_context())

    # then the discoverer ran on a worker and the store was written from the calling thread, which is
    # the thread-affinity contract every EventStore has (docs/04 §2.4)
    assert discoverer.threads and discoverer.threads[0] != main_thread
    assert spy.save_threads == [main_thread]


# --- the shared execution rules ------------------------------------------------


def test_a_transient_failure_should_be_retried_with_backoff(store: SqliteEventStore) -> None:
    # given a source that fails once transiently and then succeeds
    clock = FakeClock(start=START)
    discoverer = _FlakyDiscoverer()

    # when discovery runs
    run = run_discovery(
        [discoverer],
        store=store,
        context=make_discovery_context(
            settings=Settings(block_retries=1, retry_base_delay_seconds=1.0),
            clock=clock,
        ),
    )

    # then the retry happened on the shared clock, with the documented first backoff of base plus jitter,
    # and the eventual success was persisted. This is the same machinery a block gets, not a copy
    assert discoverer.calls == 2
    assert len(clock.sleeps) == 1
    assert 1.0 <= clock.sleeps[0] <= 2.0
    assert run.outcomes[0].new == 1


def test_a_discoverer_that_overruns_its_budget_should_time_out_without_stopping_the_run(
    store: SqliteEventStore,
) -> None:
    # given a slow discoverer and a healthy one, with a half-second budget and no retries
    clock = FakeClock(start=START)
    settings = Settings(block_timeout_seconds=0.5, block_retries=0)

    # when discovery runs
    run = run_discovery(
        [_AdvancingDiscoverer(1.0), _YieldingDiscoverer("L1")],
        store=store,
        context=make_discovery_context(settings=settings, clock=clock),
    )

    # then the slow one is TIMED_OUT, named with the discoverer vocabulary, and the other still persisted
    slow, healthy = run.outcomes
    assert slow.error_type == "TimeoutError"
    assert (
        slow.error_message is not None
        and "discover() exceeded its 0.5 s budget" in slow.error_message
    )
    assert healthy.new == 1
    assert [event.event_id for event in store.pending()] == ["L1"]


# --- the declared type is enforced ---------------------------------------------


def test_a_discoverer_yielding_a_foreign_event_type_should_save_nothing(
    store: SqliteEventStore,
) -> None:
    # given a discoverer that declares WAFER_FAILURE and yields MAINTENANCE
    # when discovery runs
    run = run_discovery([_WrongTypeDiscoverer()], store=store, context=make_discovery_context())

    # then it is that discoverer's failure and the event was not saved: a row in the wrong queue would
    # surface later, in processing, with no trace back to this source
    outcome = run.outcomes[0]
    assert outcome.error_type == "ConfigurationError"
    assert (
        outcome.error_message is not None and "owns exactly one event type" in outcome.error_message
    )
    assert store.pending() == []


def test_a_discoverer_yielding_something_that_is_not_an_event_should_save_nothing(
    store: SqliteEventStore,
) -> None:
    run = run_discovery([_NotAnEventDiscoverer()], store=store, context=make_discovery_context())

    assert run.outcomes[0].error_type == "ConfigurationError"
    assert store.pending() == []


def test_run_discovery_with_no_discoverers_should_raise_configuration_error(
    store: SqliteEventStore,
) -> None:
    with pytest.raises(ConfigurationError, match="at least one discoverer"):
        run_discovery([], store=store, context=make_discovery_context())


def test_a_discoverer_with_an_empty_event_type_should_raise_configuration_error(
    store: SqliteEventStore,
) -> None:
    # given a discoverer whose declared type is empty, which would still "work" and hide every row under ""
    class _UntypedDiscoverer(Discoverer):
        event_type = ""

        def discover(self, context: Any) -> Iterable[Event]:
            return iter(())

    with pytest.raises(ConfigurationError, match="non-empty string"):
        run_discovery([_UntypedDiscoverer()], store=store, context=make_discovery_context())


# --- logging and run metadata ---------------------------------------------------


def test_a_successful_discoverer_should_log_discovery_finish_with_the_contracted_fields(
    store: SqliteEventStore, caplog: pytest.LogCaptureFixture
) -> None:
    # given a successful discovery run
    caplog.set_level(logging.INFO)

    # when it runs
    run_discovery([_YieldingDiscoverer("L1", "L2")], store=store, context=make_discovery_context())

    # then the success line carries the fields docs/07 §4 promises, including the discoverer name so two
    # sources of the same type can be told apart
    record = next(r for r in caplog.records if r.message == "discovery.finish")
    # getattr: the fields are attached by `extra`, which LogRecord's type knows nothing about
    assert getattr(record, "run_id", None) == "9f2c1ab34de5"
    assert getattr(record, "discoverer", None) == "_YieldingDiscoverer"
    assert getattr(record, "event_type", None) == "WAFER_FAILURE"
    assert (getattr(record, "new", None), getattr(record, "duplicates", None)) == (2, 0)
    assert isinstance(getattr(record, "duration_ms", None), float)


def test_a_failed_discoverer_should_not_log_discovery_finish(
    store: SqliteEventStore, caplog: pytest.LogCaptureFixture
) -> None:
    # given a discoverer that fails
    caplog.set_level(logging.INFO)

    # when discovery runs
    run_discovery(
        [_FailingDiscoverer(PermanentBlockError("down"))],
        store=store,
        context=make_discovery_context(),
    )

    # then the failure is logged under its own event and no finish line claims a persistence that did
    # not happen
    messages = [record.message for record in caplog.records]
    assert "discovery.failed" in messages
    assert "discovery.finish" not in messages


def test_the_run_should_carry_the_context_identity_and_its_finish_time(
    store: SqliteEventStore,
) -> None:
    # given a clock that moves while the discoverer works, so the two timestamps are distinguishable --
    # with a frozen clock, reading run.started_at for finished_at would also pass
    clock = FakeClock(start=DEFAULT_START)
    context = make_discovery_context(clock=clock)

    # when discovery runs a discoverer that spends five simulated seconds
    run = run_discovery([_AdvancingDiscoverer(5.0)], store=store, context=context)

    # then the run is identified by the context's run, starts when it started, and finishes at the
    # clock's current reading
    assert run.run_id == context.run.run_id
    assert run.started_at == DEFAULT_START
    assert run.finished_at == DEFAULT_START + timedelta(seconds=5)


# --- data shape is validated per source, not left to the store -------------------


def test_a_discoverer_yielding_a_naive_timestamp_should_fail_alone(
    store: SqliteEventStore,
) -> None:
    # given a source that builds its events with a naive datetime -- the mistake a new discoverer makes
    class _NaiveDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            yield Event("WAFER_FAILURE", "x", {}, datetime(2026, 10, 18, 12, 0), "src")

    # when it runs beside a healthy source
    run = run_discovery(
        [_NaiveDiscoverer(), _YieldingDiscoverer("L1")],
        store=store,
        context=make_discovery_context(),
    )

    # then it is that discoverer's failure and the healthy source still persisted. The store would have
    # raised a plain ValueError on the main thread, aborting the run and losing the other source's events
    assert run.outcomes[0].error_type == "ConfigurationError"
    assert run.outcomes[0].error_message is not None and "naive" in run.outcomes[0].error_message
    assert [event.event_id for event in store.pending()] == ["L1"]


def test_a_discoverer_yielding_an_unserialisable_payload_should_fail_alone(
    store: SqliteEventStore,
) -> None:
    # given a source whose payload contains a value JSON cannot represent
    class _UnserialisableDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            yield Event(
                "WAFER_FAILURE",
                "x",
                {"when": datetime(2026, 10, 18, 12, 0, tzinfo=UTC)},
                datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
                "src",
            )

    run = run_discovery(
        [_UnserialisableDiscoverer(), _YieldingDiscoverer("L1")],
        store=store,
        context=make_discovery_context(),
    )

    # then it is isolated to that source, and the message says why
    assert run.outcomes[0].error_type == "ConfigurationError"
    assert run.outcomes[0].error_message is not None
    assert "not JSON-serialisable" in run.outcomes[0].error_message
    assert [event.event_id for event in store.pending()] == ["L1"]


def test_two_discoverers_of_the_same_class_should_raise_configuration_error(
    store: SqliteEventStore,
) -> None:
    # given two instances of one class, which outcomes and log lines could not tell apart
    # when discovery runs
    # then the ambiguity is refused rather than logged as two identical sources
    with pytest.raises(ConfigurationError, match="same class"):
        run_discovery(
            [_YieldingDiscoverer("A"), _YieldingDiscoverer("B")],
            store=store,
            context=make_discovery_context(),
        )


# --- the hang guard, for discoverers -------------------------------------------


def test_a_wedged_discoverer_should_be_abandoned_and_named_as_a_discoverer(
    store: SqliteEventStore, caplog: pytest.LogCaptureFixture
) -> None:
    # given a discoverer wedged on real I/O and a guard of about a second (0.05 s budget + grace).
    # This is one of the two tests in the suite that waits on real time, deliberately: the hang guard
    # exists because a wedged worker never advances the injected clock (ADR-0027)
    release = threading.Event()

    class _WedgedDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            release.wait(timeout=5.0)
            return ()

    caplog.set_level(logging.INFO)
    settings = Settings(block_timeout_seconds=0.05, block_retries=0)

    try:
        # when discovery runs
        run = run_discovery(
            [_WedgedDiscoverer()],
            store=store,
            context=make_discovery_context(settings=settings),
        )
    finally:
        release.set()

    # then the run completed with a timeout outcome for that source, and the abandoned line carries the
    # discoverer vocabulary -- a swapped field name would violate docs/07 §4 silently otherwise
    assert run.outcomes[0].error_type == "TimeoutError"
    record = next(r for r in caplog.records if r.message == "discovery.abandoned")
    assert getattr(record, "discoverer", None) == "_WedgedDiscoverer"
    assert getattr(record, "overrun_s", None) is not None


def test_a_discoverer_exceeding_the_event_ceiling_should_fail_without_persisting(
    store: SqliteEventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given a source that keeps yielding, and a ceiling small enough to test cheaply
    monkeypatch.setattr("auto_html_report.application.discovery.MAX_EVENTS_PER_DISCOVERER", 2)

    class _FloodDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            for index in range(5):
                yield make_event(f"e{index}")

    # when discovery runs
    run = run_discovery([_FloodDiscoverer()], store=store, context=make_discovery_context())

    # then it is a non-retryable failure and nothing from the partial batch was saved: the ceiling exists
    # because the hang guard bounds waiting, not memory, and this drain is the only unbounded buffer fed
    # by external content
    assert run.outcomes[0].error_type == "PermanentBlockError"
    assert run.outcomes[0].error_message is not None
    assert "more than 2 events" in run.outcomes[0].error_message
    assert store.pending() == []


def test_a_discoverer_without_an_event_type_should_raise_configuration_error(
    store: SqliteEventStore,
) -> None:
    # given a subclass that forgot the ClassVar, as a new discoverer author will
    class _ForgetfulDiscoverer(Discoverer):
        def discover(self, context: Any) -> Iterable[Event]:
            return iter(())

    # when discovery runs
    # then it is a configuration error naming the attribute, not an AttributeError from deep inside
    with pytest.raises(ConfigurationError, match="non-empty string"):
        run_discovery([_ForgetfulDiscoverer()], store=store, context=make_discovery_context())


@pytest.mark.parametrize(
    "bad_event",
    [
        pytest.param(_event_with(source=None), id="missing-source"),
        pytest.param(_event_with(source=""), id="empty-source"),
        pytest.param(_event_with(event_id=None), id="missing-event-id"),
        pytest.param(_event_with(event_id=""), id="empty-event-id"),
        pytest.param(_event_with(discovered_at=1234), id="non-datetime-timestamp"),
        pytest.param(_event_with(payload=None), id="non-mapping-payload"),
    ],
)
def test_an_event_missing_a_required_field_should_fail_that_discoverer_alone(
    store: SqliteEventStore, bad_event: Event
) -> None:
    # given a source that yields an event violating one of the schema's NOT NULL columns
    class _BadDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            yield bad_event

    # when it runs beside a healthy source
    run = run_discovery(
        [_BadDiscoverer(), _YieldingDiscoverer("L1")],
        store=store,
        context=make_discovery_context(),
    )

    # then the bad source is the one that fails, with a configuration error rather than a database
    # integrity error, and the healthy source's event is still persisted -- the failure is contained
    assert run.outcomes[0].error_type == "ConfigurationError"
    assert [event.event_id for event in store.pending()] == ["L1"]


def test_a_discoverer_yielding_a_non_finite_number_should_fail_alone(
    store: SqliteEventStore,
) -> None:
    # given a source whose payload contains NaN, which one backend would store and the other reject
    class _NonFiniteDiscoverer(Discoverer):
        event_type = "WAFER_FAILURE"

        def discover(self, context: Any) -> Iterable[Event]:
            yield make_event("x", payload={"value": float("nan")})

    # when it runs beside a healthy source
    run = run_discovery(
        [_NonFiniteDiscoverer(), _YieldingDiscoverer("L1")],
        store=store,
        context=make_discovery_context(),
    )

    # then the payload is the contract check's problem, not the store's: that source fails alone and the
    # healthy one still persists
    assert run.outcomes[0].error_type == "ConfigurationError"
    assert run.outcomes[0].error_message is not None
    assert "JSON-serialisable" in run.outcomes[0].error_message
    assert [event.event_id for event in store.pending()] == ["L1"]

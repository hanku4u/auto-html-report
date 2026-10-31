"""The processing job: one report per pending event, and the store updated only when it is right.

The rules under test are ``docs/05-discovery-and-processing.md`` §2-§4, and the acceptance criteria that
name them: a report per pending event (13), no work for processed events (14), a missing factory that
does not consume the event (15), a failed upload that is not recorded as delivered (16), and store calls
that never leave the calling thread at any concurrency setting (19).

The store is ``FakeEventStore``, which runs the shared behaviour suite, so a divergence from the SQLite
backend fails here rather than in a demo. The writer is the real ``LocalDirectoryWriter`` against
``tmp_path`` -- a document on disk is what the acceptance criteria talk about.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Self

import pytest

from auto_html_report import registry
from auto_html_report.application.context import RenderContext, ReportContext
from auto_html_report.application.ports import EventStore, FailureRecord, SaveResult
from auto_html_report.domain.block import Block, BlockStatus
from auto_html_report.domain.errors import (
    ConfigurationError,
    OutputError,
    PermanentBlockError,
    StoreError,
)
from auto_html_report.domain.event import Event
from auto_html_report.domain.report import OutputLocation, ReportDefinition
from auto_html_report.exit_codes import ExitCode, exit_code_for
from auto_html_report.infrastructure.settings import Settings
from auto_html_report.infrastructure.writers.local_writer import LocalDirectoryWriter
from auto_html_report.infrastructure.writers.s3_writer import S3Writer
from auto_html_report.processing import UNCLASSIFIED_FAILURE, _classify_failure, run_processing
from tests.support.builders import make_event
from tests.support.clocks import DEFAULT_START, FakeClock
from tests.support.contexts import make_report_context
from tests.support.stores import FakeEventStore

#: A type of the tests' own. ``WAFER_FAILURE`` belongs to the production family (reports/manufacturing),
#: which is registered as soon as anything imports it -- a test that registered over it would fail with
#: "a report factory is already registered".
EVENT_TYPE = "TEST_EVENT"


# --- the pieces a report is made of -------------------------------------------


class _OkBlock(Block[int]):
    """A block that succeeds and renders a cell."""

    def __init__(self, name: str = "Row") -> None:
        super().__init__(name=name)

    def collect(self, context: ReportContext) -> int:
        return 1

    def render(self, data: int, context: RenderContext) -> str:
        return "<p>ok</p>"


class _FailingBlock(Block[int]):
    """A block that fails permanently, so it costs one attempt rather than three."""

    def __init__(self, name: str = "Broken row") -> None:
        super().__init__(name=name)

    def collect(self, context: ReportContext) -> int:
        raise PermanentBlockError("upstream is down")

    def render(self, data: int, context: RenderContext) -> str:
        return ""


class _ThreadRecordingBlock(Block[int]):
    """Records which thread the block actually ran on, to prove the workers are real."""

    def __init__(self, threads: list[int], name: str = "Thread row") -> None:
        super().__init__(name=name)
        self._threads = threads

    def collect(self, context: ReportContext) -> int:
        self._threads.append(threading.get_ident())
        return 1

    def render(self, data: int, context: RenderContext) -> str:
        return "<p>ok</p>"


class _FailingWriter:
    """A writer that cannot deliver, for acceptance criterion 16."""

    def write(self, *, filename: str, content: str) -> OutputLocation:
        raise OutputError("the bucket is on fire")


def _register(
    blocks: Callable[[], Iterable[Block[Any]]],
    *,
    event_type: str = EVENT_TYPE,
) -> None:
    """Register a factory that builds one report from the event's identity."""

    def factory(event: Event, context: ReportContext) -> ReportDefinition:
        return ReportDefinition(
            title=f"Report {event.event_id}",
            event_id=event.event_id,
            blocks=list(blocks()),
        )

    registry.register_report(event_type)(factory)


@pytest.fixture(autouse=True)
def _restore_report_factories() -> Iterator[None]:
    """Snapshot and restore the report-factory table around every test.

    The registry is process-global, so a factory registered here would leak into every later test in
    the same process (``docs/09`` §2 rule 4). The snapshot is taken *before* the test adds anything and
    restored afterwards -- factories land on import, and no scan is involved on this side.
    """
    saved = dict(registry._report_factories)
    yield
    registry._report_factories.clear()
    registry._report_factories.update(saved)


def _context(
    *,
    max_concurrent_reports: int = 1,
    fail_on_block_error: bool = True,
    processing_limit: int = 500,
) -> ReportContext:
    return make_report_context(
        settings=Settings(
            max_concurrent_reports=max_concurrent_reports,
            fail_on_block_error=fail_on_block_error,
            processing_limit=processing_limit,
        ),
        clock=FakeClock(start=DEFAULT_START),
    )


# --- the normal path ------------------------------------------------------------


def test_run_processing_should_build_and_write_a_report_for_each_pending_event(
    tmp_path: Path,
) -> None:
    # given two pending events and a factory that renders a report for each
    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event("a", event_type=EVENT_TYPE), make_event("b", event_type=EVENT_TYPE)])
    writer = LocalDirectoryWriter(tmp_path)

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=writer)

    # then each event produced a written report, and each was recorded as delivered (criterion 13)
    assert [outcome.event.event_id for outcome in run.outcomes] == ["a", "b"]
    assert all(outcome.error_type is None for outcome in run.outcomes)
    assert all(outcome.output_uri is not None for outcome in run.outcomes)
    assert all(outcome.rows[0].status is BlockStatus.OK for outcome in run.outcomes)
    assert store.pending() == []
    assert len(list(tmp_path.glob("*.html"))) == 2
    assert run.all_succeeded


def test_a_second_run_should_do_no_work_for_already_processed_events(tmp_path: Path) -> None:
    # given a run that delivered both events
    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event("a", event_type=EVENT_TYPE), make_event("b", event_type=EVENT_TYPE)])
    run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # when processing runs again
    second = run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then there is nothing pending and nothing is rebuilt (criterion 14)
    assert second.outcomes == ()
    assert second.all_succeeded
    assert len(list(tmp_path.glob("*.html"))) == 2


def test_event_type_and_limit_should_be_passed_through_to_pending(tmp_path: Path) -> None:
    # given two pending events of different types
    _register(lambda: [_OkBlock()], event_type=EVENT_TYPE)
    _register(lambda: [_OkBlock()], event_type="MAINTENANCE")
    store = FakeEventStore()
    store.save(
        [
            make_event("a", event_type=EVENT_TYPE),
            make_event("b", event_type=EVENT_TYPE),
            make_event("m", event_type="MAINTENANCE"),
        ]
    )

    # when processing is restricted to one type and one event
    run = run_processing(
        store=store,
        context=_context(),
        writer=LocalDirectoryWriter(tmp_path),
        event_type=EVENT_TYPE,
        limit=1,
    )

    # then only that event was processed and the others are untouched
    assert [outcome.event.event_id for outcome in run.outcomes] == ["a"]
    # same discovered_at, so the queue tie-breaks on event type: MAINTENANCE before WAFER_FAILURE
    assert [event.event_id for event in store.pending()] == ["m", "b"]


def test_run_processing_with_nothing_pending_should_return_an_empty_run(tmp_path: Path) -> None:
    # given an empty queue
    # when processing runs
    run = run_processing(
        store=FakeEventStore(), context=_context(), writer=LocalDirectoryWriter(tmp_path)
    )

    # then it succeeds vacuously rather than being an error
    assert run.outcomes == ()
    assert run.all_succeeded
    assert exit_code_for(run) is ExitCode.SUCCESS


# --- failures that must not consume an event -----------------------------------


def test_a_missing_factory_should_leave_the_event_new_and_record_a_configuration_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # given a pending event whose type has no registered factory, as a half-deployed job produces
    store = FakeEventStore()
    store.save([make_event("a", event_type="NOT_DEPLOYED")])
    caplog.set_level(logging.INFO)

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then the event is still pending for the next run, and the outcome says why (criterion 15)
    assert run.outcomes[0].error_type == "ConfigurationError"
    assert [event.event_id for event in store.pending()] == ["a"]
    assert exit_code_for(run) is ExitCode.CONFIGURATION_ERROR

    # and the log says "skipped", not "failed": no attempt was counted
    messages = [record.message for record in caplog.records]
    assert "event.skipped" in messages
    assert "event.failed" not in messages


def test_a_factory_raising_a_data_error_should_count_an_attempt_and_keep_the_event(
    tmp_path: Path,
) -> None:
    # given a factory that cannot read the payload it was given, as a malformed upstream response causes
    def factory(event: Event, context: ReportContext) -> ReportDefinition:
        raise KeyError("lot_id")

    registry.register_report(EVENT_TYPE)(factory)
    store = FakeEventStore(max_attempts=2)
    store.save([make_event("a", event_type=EVENT_TYPE)])

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then it is the event's failure, not the run's: one attempt is consumed and the event stays queued
    # for a retry, which is what an upstream fix needs
    assert run.outcomes[0].error_type == "KeyError"
    assert [event.event_id for event in store.pending()] == ["a"]
    assert exit_code_for(run) is ExitCode.PARTIAL_FAILURE


def test_a_failed_upload_should_not_mark_the_event_processed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # given a writer that cannot deliver
    _register(lambda: [_OkBlock()])
    store = FakeEventStore(max_attempts=2)
    store.save([make_event("a", event_type=EVENT_TYPE)])
    caplog.set_level(logging.INFO)

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=_FailingWriter())

    # then the event is not recorded as delivered (criterion 16), the attempt is counted, and the exit
    # code is the output one -- a report nobody can find is not a success
    assert run.outcomes[0].error_type == "OutputError"
    assert [event.event_id for event in store.pending()] == ["a"]
    assert exit_code_for(run) is ExitCode.OUTPUT_ERROR
    failed = next(r for r in caplog.records if r.message == "event.failed")
    assert getattr(failed, "attempts", None) == 1


def test_a_failed_upload_at_the_attempt_limit_should_retire_the_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # given a store that allows exactly one attempt
    _register(lambda: [_OkBlock()])
    store = FakeEventStore(max_attempts=1)
    store.save([make_event("a", event_type=EVENT_TYPE)])
    caplog.set_level(logging.INFO)

    # when the upload fails
    run = run_processing(store=store, context=_context(), writer=_FailingWriter())

    # then the event is retired and the log says so, so an operator can find it without querying
    assert store.pending() == []
    assert run.outcomes[0].error_type == "OutputError"
    messages = [record.message for record in caplog.records]
    assert "event.retired" in messages
    retired = next(r for r in caplog.records if r.message == "event.retired")
    assert getattr(retired, "attempts", None) == 1


# --- failed blocks --------------------------------------------------------------


def test_failed_blocks_should_fail_the_event_when_the_setting_says_so(tmp_path: Path) -> None:
    # given a report whose only block fails, with the documented default (fail on block error)
    _register(lambda: [_FailingBlock()])
    store = FakeEventStore()
    store.save([make_event("a", event_type=EVENT_TYPE)])

    # when processing runs
    run = run_processing(
        store=store,
        context=_context(fail_on_block_error=True),
        writer=LocalDirectoryWriter(tmp_path),
    )

    # then the document was still written -- the error cell is the deliverable -- but the event is marked
    # failed for follow-up and the exit code is a partial failure
    outcome = run.outcomes[0]
    assert outcome.error_type == "BlockError"
    assert outcome.output_uri is not None
    assert outcome.rows[0].status is BlockStatus.FAILED
    assert [event.event_id for event in store.pending()] == ["a"]
    assert exit_code_for(run) is ExitCode.PARTIAL_FAILURE


def test_failed_blocks_should_still_be_delivered_when_the_setting_is_off(tmp_path: Path) -> None:
    # given an operator who considers a partly-red report acceptable
    _register(lambda: [_FailingBlock()])
    store = FakeEventStore()
    store.save([make_event("a", event_type=EVENT_TYPE)])

    # when processing runs with the setting off
    run = run_processing(
        store=store,
        context=_context(fail_on_block_error=False),
        writer=LocalDirectoryWriter(tmp_path),
    )

    # then the event is delivered and the run exits successfully: the error cells are the report
    assert run.outcomes[0].error_type is None
    assert store.pending() == []
    assert exit_code_for(run) is ExitCode.SUCCESS


def test_the_per_call_override_should_win_over_the_setting(tmp_path: Path) -> None:
    # given a setting that would fail the event, and a caller that says otherwise
    _register(lambda: [_FailingBlock()])
    store = FakeEventStore()
    store.save([make_event("a", event_type=EVENT_TYPE)])

    # when processing runs with the override
    run = run_processing(
        store=store,
        context=_context(fail_on_block_error=True),
        writer=LocalDirectoryWriter(tmp_path),
        fail_events_with_failed_blocks=False,
    )

    # then the caller's explicit choice decides, not the ambient setting
    assert run.outcomes[0].error_type is None
    assert store.pending() == []


# --- concurrency and the store's thread affinity --------------------------------


class _RecordingStore:
    """Wraps a store, recording the thread of every call."""

    def __init__(self, inner: EventStore) -> None:
        self._inner = inner
        self.calls: list[tuple[str, int]] = []

    def save(self, events: Iterable[Event]) -> SaveResult:
        self.calls.append(("save", threading.get_ident()))
        return self._inner.save(events)

    def pending(self, *, event_type: str | None = None, limit: int | None = None) -> list[Event]:
        self.calls.append(("pending", threading.get_ident()))
        return self._inner.pending(event_type=event_type, limit=limit)

    def mark_processed(self, event: Event, *, output_uri: str) -> None:
        self.calls.append(("mark_processed", threading.get_ident()))
        self._inner.mark_processed(event, output_uri=output_uri)

    def mark_failed(self, event: Event, *, reason: str) -> FailureRecord:
        self.calls.append(("mark_failed", threading.get_ident()))
        return self._inner.mark_failed(event, reason=reason)

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def test_store_calls_should_all_happen_on_the_calling_thread_at_any_concurrency(
    tmp_path: Path,
) -> None:
    # given four events, four reports in flight, and a block that records the thread it runs on
    block_threads: list[int] = []
    _register(lambda: [_ThreadRecordingBlock(block_threads)])
    store = _RecordingStore(FakeEventStore())
    store.save([make_event(letter, event_type=EVENT_TYPE) for letter in "abcd"])
    main_thread = threading.get_ident()

    # when processing runs with report concurrency above one
    run_processing(
        store=store,
        context=_context(max_concurrent_reports=4),
        writer=LocalDirectoryWriter(tmp_path),
    )

    # then every store call came from the calling thread (criterion 19), while the reports were actually
    # built somewhere else -- otherwise this test would pass with no concurrency at all
    assert {thread for _, thread in store.calls} == {main_thread}
    assert block_threads and all(thread != main_thread for thread in block_threads)


def test_a_store_failure_while_recording_should_stop_the_run_and_be_recorded(
    tmp_path: Path,
) -> None:
    # given a store that fails when marking the second event processed
    _register(lambda: [_OkBlock()])
    inner = FakeEventStore()

    class _FailingMarkStore(_RecordingStore):
        def mark_processed(self, event: Event, *, output_uri: str) -> None:
            if event.event_id == "b":
                raise StoreError("the disk went away")
            super().mark_processed(event, output_uri=output_uri)

    store = _FailingMarkStore(inner)
    store.save([make_event(letter, event_type=EVENT_TYPE) for letter in "abc"])

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then the failure is recorded on that event, the run stops rather than reporting success, and the
    # exit code is the store one (ADR-0030)
    assert [outcome.event.event_id for outcome in run.outcomes] == ["a", "b"]
    assert run.outcomes[1].error_type == "StoreError"
    assert exit_code_for(run) is ExitCode.STORE_ERROR
    assert [event.event_id for event in inner.pending()] == ["b", "c"]


# --- logging --------------------------------------------------------------------


def test_a_processed_event_should_log_the_contracted_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # given a successful run
    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event("a", event_type=EVENT_TYPE)])
    caplog.set_level(logging.INFO)

    context = _context()

    # when processing runs
    run_processing(store=store, context=context, writer=LocalDirectoryWriter(tmp_path))

    # then event.processed carries the fields docs/07 §4 promises
    record = next(r for r in caplog.records if r.message == "event.processed")
    assert getattr(record, "run_id", None) == context.run.run_id
    assert getattr(record, "event_type", None) == EVENT_TYPE
    assert getattr(record, "event_id", None) == "a"
    assert str(getattr(record, "output_uri", "")).startswith("file://")


def test_a_failed_upload_should_stop_the_run_after_one_recorded_outcome(
    tmp_path: Path,
) -> None:
    # given three pending events and a writer that fails for every one, as a bucket outage does
    _register(lambda: [_OkBlock()])
    inner = FakeEventStore()
    inner.save([make_event(letter, event_type=EVENT_TYPE) for letter in "abc"])
    store = _RecordingStore(inner)

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=_FailingWriter())

    # then only the first event consumed an attempt: continuing would burn all three and retire a
    # healthy queue over a temporary infrastructure problem. The failed event stays queued -- one failed
    # attempt is retried, which is the point -- but nothing touched the other two
    assert [outcome.event.event_id for outcome in run.outcomes] == ["a"]
    assert run.outcomes[0].error_type == "OutputError"
    assert [name for name, _ in store.calls].count("mark_failed") == 1
    assert [event.event_id for event in inner.pending()] == ["a", "b", "c"]
    assert exit_code_for(run) is ExitCode.OUTPUT_ERROR


def test_a_pending_failure_should_escape_because_there_is_no_run_to_report(
    tmp_path: Path,
) -> None:
    # given a store that cannot even read its queue
    class _UnreadableStore(_RecordingStore):
        def pending(
            self, *, event_type: str | None = None, limit: int | None = None
        ) -> list[Event]:
            raise StoreError("the database file was deleted")

    store = _UnreadableStore(FakeEventStore())

    # when processing runs
    # then it raises: with no outcomes there is nothing for exit_code_for to rank, so the script must
    # map this one itself (docs/05 §6)
    with pytest.raises(StoreError, match="deleted"):
        run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))


# --- the batch ceiling ----------------------------------------------------------


def test_the_configured_processing_limit_should_cap_one_run(tmp_path: Path) -> None:
    # given three pending events and a ceiling of two per run
    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event(letter, event_type=EVENT_TYPE) for letter in "abc"])

    # when processing runs without an explicit limit
    run = run_processing(
        store=store,
        context=_context(processing_limit=2),
        writer=LocalDirectoryWriter(tmp_path),
    )

    # then the run took two and left the rest for the next one, rather than draining the queue into
    # memory and one future per event
    assert [outcome.event.event_id for outcome in run.outcomes] == ["a", "b"]
    assert [event.event_id for event in store.pending()] == ["c"]


def test_an_explicit_limit_above_the_ceiling_should_be_clamped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # given a caller asking for more than the configured ceiling
    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event(letter, event_type=EVENT_TYPE) for letter in "abc"])
    caplog.set_level(logging.INFO)

    # when processing runs
    run = run_processing(
        store=store,
        context=_context(processing_limit=1),
        writer=LocalDirectoryWriter(tmp_path),
        limit=3,
    )

    # then the ceiling wins and the log says so: silently processing fewer events than asked would be
    # indistinguishable from a queue that was shorter
    assert len(run.outcomes) == 1
    record = next(r for r in caplog.records if r.message == "processing.capped")
    assert (getattr(record, "requested", None), getattr(record, "limit", None)) == (3, 1)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_should_raise_value_error(tmp_path: Path, limit: int) -> None:
    # given a limit that would process nothing, which a green build would hide
    # when processing runs
    # then it is refused as a caller error, before the store is asked anything
    with pytest.raises(ValueError, match="at least 1"):
        run_processing(
            store=FakeEventStore(),
            context=_context(),
            writer=LocalDirectoryWriter(tmp_path),
            limit=limit,
        )


def test_an_unregistered_event_type_filter_should_raise_configuration_error(
    tmp_path: Path,
) -> None:
    # given a typo'd event type, whose queue read would be empty and whose exit code would be SUCCESS
    # when processing runs
    # then the filter is validated up front: a job that processes nothing for ever must be loud
    with pytest.raises(ConfigurationError, match="no report factory"):
        run_processing(
            store=FakeEventStore(),
            context=_context(),
            writer=LocalDirectoryWriter(tmp_path),
            event_type="WAFER_FAILUER",
        )


# --- failure classification ------------------------------------------------------


def test_one_events_data_error_should_not_stop_the_others(tmp_path: Path) -> None:
    # given a factory that fails on one event's payload and succeeds on the next
    def factory(event: Event, context: ReportContext) -> ReportDefinition:
        if event.event_id == "bad":
            raise KeyError("lot_id")
        return ReportDefinition(title="Fine", event_id=event.event_id, blocks=[_OkBlock()])

    registry.register_report(EVENT_TYPE)(factory)
    store = FakeEventStore(max_attempts=2)
    store.save(
        [
            make_event("bad", event_type=EVENT_TYPE),
            make_event("good", event_type=EVENT_TYPE),
        ]
    )

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then both events have outcomes, the second was delivered, and only the first is queued for a retry
    assert [outcome.event.event_id for outcome in run.outcomes] == ["bad", "good"]
    assert [outcome.error_type for outcome in run.outcomes] == ["KeyError", None]
    assert [event.event_id for event in store.pending()] == ["bad"]
    assert exit_code_for(run) is ExitCode.PARTIAL_FAILURE


def test_a_refused_definition_should_count_an_attempt_and_retire(
    tmp_path: Path,
) -> None:
    # given a factory whose definition the validator refuses -- the natural result of building a title
    # or block list from payload data
    def factory(event: Event, context: ReportContext) -> ReportDefinition:
        raise ConfigurationError("a report must have a title")

    registry.register_report(EVENT_TYPE)(factory)
    store = FakeEventStore(max_attempts=1)
    store.save([make_event("a", event_type=EVENT_TYPE)])

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then it counts as an attempt and retires: leaving it uncounted would pin it at the head of an
    # oldest-first queue for ever, and ranking it as a deployment error would send CI after the wrong
    # cause (ADR-0019)
    assert run.outcomes[0].error_type == "EventDataError"
    assert store.pending() == []
    assert exit_code_for(run) is ExitCode.PARTIAL_FAILURE


def test_a_writer_subclass_should_be_classified_by_type_not_by_name(tmp_path: Path) -> None:
    # given a third-party writer whose error subclasses OutputError, as a real adapter would
    class _S3WriterError(OutputError):
        pass

    class _SubclassedWriter:
        def write(self, *, filename: str, content: str) -> OutputLocation:
            raise _S3WriterError("the bucket refused the object")

    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event(letter, event_type=EVENT_TYPE) for letter in "ab"])

    # when processing runs
    run = run_processing(
        store=store, context=_context(max_concurrent_reports=4), writer=_SubclassedWriter()
    )

    # then the subclass is filed as an OutputError -- it stopped the run and exits 4, rather than being
    # treated as an unknown per-event failure and continuing through the queue
    assert len(run.outcomes) == 1
    assert run.outcomes[0].error_type == "OutputError"
    assert exit_code_for(run) is ExitCode.OUTPUT_ERROR


def test_a_failing_store_should_be_logged_as_store_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # given a store that fails while recording the first event
    _register(lambda: [_OkBlock()])

    class _BrokenStore(_RecordingStore):
        def mark_processed(self, event: Event, *, output_uri: str) -> None:
            raise StoreError("the disk went away")

    store = _BrokenStore(FakeEventStore())
    store.save([make_event("a", event_type=EVENT_TYPE)])
    caplog.set_level(logging.INFO)

    # when processing runs
    run_processing(store=store, context=_context(), writer=LocalDirectoryWriter(tmp_path))

    # then store.failed carries the diagnosis, which is what an operator needs (docs/07 §4)
    messages = [record.message for record in caplog.records]
    assert "store.failed" in messages
    assert "event.processed" not in messages


@pytest.mark.parametrize("name", ["OutputError", "StoreError", "ConfigurationError"])
def test_a_class_that_merely_shares_a_framework_name_should_be_unclassified(
    name: str,
) -> None:
    # given an unrelated class from another library whose name collides with a framework error
    impostor = type(name, (Exception,), {})

    # when it is classified
    # then it cannot borrow the framework's control flow: a lookalike must not abort the run or choose
    # an exit code, and the classification says so rather than the colliding name
    assert _classify_failure(impostor("boom")) == UNCLASSIFIED_FAILURE


def test_the_classification_should_still_recognise_the_real_types_and_their_subclasses() -> None:
    # given the framework's own errors, a subclass of one, and an ordinary exception
    class _S3WriterError(OutputError):
        pass

    # when they are classified
    # then subclasses are filed with their base (the isinstance rule) and unrelated types keep their
    # own name, because the outcome's error_type is also the diagnosis
    assert _classify_failure(_S3WriterError("no bucket")) == "OutputError"
    assert _classify_failure(StoreError("gone")) == "StoreError"
    assert _classify_failure(ConfigurationError("refused")) == "EventDataError"
    assert _classify_failure(KeyError("lot_id")) == "KeyError"


def test_an_impostor_writer_error_should_not_stop_the_run_or_pick_the_output_code(
    tmp_path: Path,
) -> None:
    # given a writer raising a class that is named OutputError but is not the framework's
    Impostor = type("OutputError", (Exception,), {})

    class _ImpostorWriter:
        def write(self, *, filename: str, content: str) -> OutputLocation:
            raise Impostor("a library-specific failure")

    _register(lambda: [_OkBlock()])
    store = FakeEventStore()
    store.save([make_event(letter, event_type=EVENT_TYPE) for letter in "ab"])

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=_ImpostorWriter())

    # then it is an ordinary per-event failure: the run continued, both events counted an attempt, and
    # the exit code is the partial one -- not the output code a real OutputError would select
    assert [outcome.event.event_id for outcome in run.outcomes] == ["a", "b"]
    assert [outcome.error_type for outcome in run.outcomes] == [
        UNCLASSIFIED_FAILURE,
        UNCLASSIFIED_FAILURE,
    ]
    assert run.outcomes[0].error_message is not None
    assert run.outcomes[0].error_message.startswith("OutputError: ")
    assert exit_code_for(run) is ExitCode.PARTIAL_FAILURE


@pytest.mark.parametrize("value", [0, -5])
def test_a_non_positive_configured_limit_should_raise_configuration_error(
    tmp_path: Path, value: int
) -> None:
    # given a directly constructed Settings whose ceiling would process nothing
    context = make_report_context(
        settings=Settings(processing_limit=value), clock=FakeClock(start=DEFAULT_START)
    )

    # when processing runs
    # then it refuses instead of reporting a healthy empty run -- the loader bounds this, but the loader
    # is not the only way to build Settings
    with pytest.raises(ConfigurationError, match="at least 1"):
        run_processing(
            store=FakeEventStore(), context=context, writer=LocalDirectoryWriter(tmp_path)
        )


def test_processing_should_record_an_s3_uri_and_mark_the_event_processed() -> None:
    # given the S3 writer backed by a stub client -- no network, no credentials
    order: list[str] = []

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def put_object(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            order.append("upload")
            return {}

    class _OrderRecordingStore(FakeEventStore):
        def mark_processed(self, event: Event, *, output_uri: str) -> None:
            order.append("mark_processed")
            super().mark_processed(event, output_uri=output_uri)

    client = _Client()
    writer = S3Writer("reports-bucket", prefix="team", client=client)
    _register(lambda: [_OkBlock()])
    store = _OrderRecordingStore()
    store.save([make_event("a", event_type=EVENT_TYPE)])

    # when processing runs
    run = run_processing(store=store, context=_context(), writer=writer)

    # then the delivered URI names the bucket and key, and the event is only then marked processed
    outcome = run.outcomes[0]
    assert outcome.output_uri is not None
    assert outcome.output_uri.startswith("s3://reports-bucket/team/")
    assert store.pending() == []
    assert str(client.calls[0]["Key"]).startswith("team/")
    # and the upload happened first: marking processed before the write would record a report nobody has
    assert order == ["upload", "mark_processed"]

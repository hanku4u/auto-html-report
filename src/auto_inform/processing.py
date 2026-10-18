"""The processing job: turn pending events into written reports, and record what happened.

This is the second of the two scheduled jobs (``docs/05-discovery-and-processing.md`` §2). Discovery
persists events; processing drains them, and the store is the only thing the two share.

**The order per event is load-bearing.** A report is marked ``PROCESSED`` only after the writer has
returned (``docs/05`` §3). An upload that failed leaves the event to be retried, because a document
nobody can find is not delivered. A report whose *blocks* failed is different: those error cells are what
the document is, so the event is delivered unless ``AUTO_INFORM_FAIL_ON_BLOCK_ERROR`` says otherwise.

**The store is touched on the calling thread only.** Reports are built concurrently up to
``AUTO_INFORM_MAX_CONCURRENT_REPORTS`` (ADR-0020), but workers only collect, render and write; every
``pending``/``mark_*`` call happens in the commit loop below, in event order. That is what makes the
thread-affine ``EventStore`` contract hold at any concurrency setting (acceptance criterion 19).

**A run takes a bounded batch.** ``AUTO_INFORM_PROCESSING_LIMIT`` (default 500) is the ceiling on events
one run pulls from the queue; an explicit ``limit`` can lower it, never raise it. A scheduled job that
needs more than one batch runs more often, and a backlog is sharded by event type rather than drained in
one enormous run (``docs/05`` §4). Workers are submitted as a sliding window of at most
``max_concurrent_reports``, so a large batch does not become one future per event.

**Failure classification drives control flow, so it is by type, not by name.** A writer's
``class S3WriterError(OutputError)`` must stop the run and exit 4; an unrelated class that merely shares
the name ``OutputError`` must do neither. ``_classify_failure`` uses ``isinstance`` for exactly that
reason.

**Failures belong to whoever owns them.** A missing factory is a deployment problem: the event stays
``NEW``, no attempt is counted, and the run exits ``2``. A definition refused because of the event's own
data is that event's problem: it counts as an attempt and retires after the limit, so a poison event
cannot sit at the head of the queue for ever. Writer and store failures are systemic: recorded once, then
the run stops, because every later event would fail the same way and a writer outage must not burn the
whole queue's attempt budget.

**Operational failures become outcomes, not exceptions.** ``exit_code_for`` ranks them, and the job script
still gets a ``ProcessingRun``. Only a failure that leaves nothing to report on -- ``pending()`` itself
failing -- escapes. This is the interpretation recorded in ADR-0030.

**No report-level deadline, deliberately.** Every block is bounded by its own budget and the hang guard
(ADR-0027); factories are documented as pure and fast (``docs/03`` §3), so a *factory* that blocks is a
defect in user code, like an ``__init__`` that never returns. Inventing a timeout for it would abandon
legitimate large reports and hide the defect; the limitation is recorded in ``docs/11`` instead.

**Why this module is at the package root.** It composes the registry and
:func:`~auto_inform.generate.generate_report`, both root modules, and ``application/`` is forbidden from
importing either: doing so would give every application module a path to the renderer. Same reasoning as
ADR-0028.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from auto_inform.application.context import ReportContext
from auto_inform.application.execution import summarise_exception
from auto_inform.application.ports import EventStore, OutputWriter
from auto_inform.domain.block import BlockOutcome, BlockStatus
from auto_inform.domain.errors import ConfigurationError, OutputError, StoreError
from auto_inform.domain.event import Event
from auto_inform.domain.report import ReportDefinition, ReportResult
from auto_inform.generate import generate_report
from auto_inform.registry import get_report_factory

#: The outcome vocabulary. These strings are what ``exit_code_for`` ranks, so they are constants rather
#: than three scattered string literals.
CONFIGURATION_FAILURE = "ConfigurationError"
STORE_FAILURE = "StoreError"
OUTPUT_FAILURE = "OutputError"
#: A definition the renderer refused (empty title, no blocks, an impossible block override). It belongs to
#: the event -- a factory that derives a title or block list from the payload can produce one -- so it is
#: counted as an attempt and ranked as a partial failure, not as a deployment error.
EVENT_DATA_FAILURE = "EventDataError"
BLOCK_FAILURE = "BlockError"
#: An exception whose class name collides with a framework error but which is not one. It must not borrow
#: the framework's control flow, and it must not pretend to be unclassifiable either.
UNCLASSIFIED_FAILURE = "UnclassifiedError"

#: The names the abort policy and the exit ranking key on. Only the ``isinstance`` checks in
#: ``_classify_failure`` may produce them.
_RESERVED_FAILURE_NAMES: frozenset[str] = frozenset(
    {CONFIGURATION_FAILURE, STORE_FAILURE, OUTPUT_FAILURE}
)

#: Failure kinds that stop the run after being recorded. Both mean the environment is broken, not the
#: event, so every later event would fail the same way.
_ABORTING_FAILURES: frozenset[str] = frozenset({STORE_FAILURE, OUTPUT_FAILURE})

#: Matches ``application.execution``'s cap: a message is for a log line, not a novel.
_REASON_LIMIT = 300

#: How many failed block names a reason lists before it summarises the rest.
_REASON_NAMES = 5


@dataclass(frozen=True, slots=True)
class EventProcessingOutcome:
    """What happened to one event.

    ``report_title`` is empty and ``rows`` is empty when report building raised before returning a
    rendered document. ``output_uri`` is set whenever a document was written, whether or not the event
    was then marked ``PROCESSED`` -- a report with failed blocks can be delivered *and* marked failed for
    follow-up. ``error_type`` is set when the event did **not** succeed; its value is the classification
    :func:`~auto_inform.exit_codes.exit_code_for` ranks.
    """

    event: Event
    report_title: str
    rows: tuple[BlockOutcome, ...]
    output_uri: str | None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    """One processing job run: one outcome per event, in queue order."""

    run_id: str
    outcomes: tuple[EventProcessingOutcome, ...]
    started_at: datetime
    finished_at: datetime

    @property
    def all_succeeded(self) -> bool:
        """Whether every event processed without error."""
        return all(outcome.error_type is None for outcome in self.outcomes)


def run_processing(
    *,
    store: EventStore,
    context: ReportContext,
    writer: OutputWriter,
    event_type: str | None = None,
    limit: int | None = None,
    fail_events_with_failed_blocks: bool | None = None,
) -> ProcessingRun:
    """Fetch pending events, build each event's report, write it, and update the store.

    ``limit`` may lower the batch below ``AUTO_INFORM_PROCESSING_LIMIT`` but not raise it; ``None`` means
    the configured ceiling. ``fail_events_with_failed_blocks`` overrides
    ``AUTO_INFORM_FAIL_ON_BLOCK_ERROR`` for this run, and ``None`` -- the default -- defers to the
    setting, which is what a job script that just calls ``run_processing(store=..., context=...,
    writer=...)`` expects.

    Raises only when there is nothing to report on: an invalid ``Settings``, an unregistered
    ``event_type`` filter, a ``limit`` below one, or a ``pending()`` failure. A store failure or a failed
    write during the commit phase is recorded on the affected outcome and stops the run.
    """
    if limit is not None and limit < 1:
        raise ValueError(
            f"limit must be at least 1; got {limit!r}. None means the configured ceiling, not zero."
        )
    if event_type is not None:
        # Validate the filter itself. Otherwise a typo'd event type reads an empty queue and exits
        # SUCCESS for ever -- the one way a misconfigured job looks permanently healthy.
        get_report_factory(event_type)

    fail_on_block_error = (
        context.settings.fail_on_block_error
        if fail_events_with_failed_blocks is None
        else fail_events_with_failed_blocks
    )

    ceiling = context.settings.processing_limit
    if ceiling < 1:
        # `load_settings` bounds this, but Settings can be constructed directly -- the same defence
        # `execution._validate` makes for the retry counts. A zero ceiling would process nothing and call
        # it success, which is the failure the event-type filter check above exists to prevent.
        raise ConfigurationError(
            f"processing_limit is {ceiling!r}; it must be at least 1 (docs/07 §1.1)"
        )

    requested = ceiling if limit is None else limit
    if requested > ceiling:
        context.logger.warning(
            "processing.capped",
            extra={
                "run_id": context.run.run_id,
                "requested": requested,
                "limit": ceiling,
            },
        )

    events = store.pending(event_type=event_type, limit=min(requested, ceiling))
    outcomes: list[EventProcessingOutcome] = []

    if events:
        workers = max(1, min(context.settings.max_concurrent_reports, len(events)))
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="auto-inform-report")
        try:
            remaining = iter(events)
            window: deque[Future[_BuiltReport]] = deque()
            for _ in range(workers):
                _submit_next(window, remaining, pool, context, writer)

            while window:
                built = window.popleft().result()
                outcome = _commit(
                    built,
                    store=store,
                    context=context,
                    fail_on_block_error=fail_on_block_error,
                )
                outcomes.append(outcome)
                if outcome.error_type in _ABORTING_FAILURES:
                    # A systemic failure: stop submitting and stop waiting. Reports already written by the
                    # window's other workers are unrecorded; the next run rebuilds them, which is the safe
                    # direction.
                    for queued in window:
                        queued.cancel()
                    window.clear()
                    break
                _submit_next(window, remaining, pool, context, writer)
        finally:
            # Not a `with` block: on the abort path that would still execute every queued future, and on
            # any exception it would wait for work nobody is going to record. Same rule as ADR-0005.
            pool.shutdown(wait=False, cancel_futures=True)

    return ProcessingRun(
        run_id=context.run.run_id,
        outcomes=tuple(outcomes),
        started_at=context.run.started_at,
        finished_at=context.clock.now(),
    )


def _submit_next(
    window: deque[Future[_BuiltReport]],
    remaining: Iterator[Event],
    pool: ThreadPoolExecutor,
    context: ReportContext,
    writer: OutputWriter,
) -> None:
    """Keep the in-flight window full without materialising a future per event."""
    event = next(remaining, None)
    if event is not None:
        window.append(pool.submit(_build_report, event, context, writer))


@dataclass(frozen=True, slots=True)
class _BuiltReport:
    """What a worker produced, before any store call. The store is not allowed in the worker."""

    event: Event
    title: str = ""
    rows: tuple[BlockOutcome, ...] = ()
    output_uri: str | None = None
    error: Exception | None = None
    #: True only for "no factory for this event type": the event stays NEW and no attempt is counted.
    missing_factory: bool = False


def _build_report(event: Event, context: ReportContext, writer: OutputWriter) -> _BuiltReport:
    """Factory, definition, report, write -- the whole per-event pipeline, in one worker."""
    try:
        factory = get_report_factory(event.event_type)
    except ConfigurationError as exc:
        # Deployment, not data: the module that registers the factory was never imported. Leaving the
        # event alone is what lets a half-deployed job pick it up after the fix (docs/05 §2 rule 2).
        return _BuiltReport(event=event, error=exc, missing_factory=True)

    definition: ReportDefinition | None = None
    try:
        definition = factory(event, context)
        result: ReportResult = generate_report(definition, context=context, writer=writer)
    except Exception as exc:  # noqa: BLE001 - every per-event failure must become an outcome
        # Deliberately broad and deliberate about *where*: classification happens at commit time, on the
        # main thread, where the store may be touched.
        title = definition.title if isinstance(definition, ReportDefinition) else ""
        return _BuiltReport(event=event, title=title, error=exc)

    return _BuiltReport(
        event=event,
        title=definition.title,
        rows=tuple(row.outcome for row in result.report.rows),
        output_uri=result.location.uri if result.location is not None else None,
    )


def _classify_failure(exc: BaseException) -> str:
    """The outcome classification for an exception, by type rather than by name.

    Subclasses must be filed with their base: a third-party writer raising
    ``class S3WriterError(OutputError)`` has to stop the run and exit 4. Conversely, an unrelated class
    that happens to be *called* ``OutputError`` must not.
    """
    if isinstance(exc, StoreError):
        return STORE_FAILURE
    if isinstance(exc, OutputError):
        return OUTPUT_FAILURE
    if isinstance(exc, ConfigurationError):
        # Reached only for a refused definition (an event-data problem); the missing-factory case never
        # gets here. Ranking it as a deployment error would send an operator after the wrong thing.
        return EVENT_DATA_FAILURE

    name = type(exc).__name__
    if name in _RESERVED_FAILURE_NAMES:
        # An unrelated class from another library that happens to be *called* ``OutputError`` is not an
        # ``OutputError``. The abort policy and the exit ranking both key on these strings, so a lookalike
        # must not stop a run or select a code; it is classified as unclassified and keeps the event's
        # own failure semantics (one attempt, partial exit).
        return UNCLASSIFIED_FAILURE
    return name


def _commit(
    built: _BuiltReport,
    *,
    store: EventStore,
    context: ReportContext,
    fail_on_block_error: bool,
) -> EventProcessingOutcome:
    """Decide the event's fate and record it. Runs on the calling thread, always."""
    failed_blocks = tuple(outcome for outcome in built.rows if outcome.status is not BlockStatus.OK)

    if built.error is not None:
        if built.missing_factory:
            # A deployment problem, not the event's: leave it NEW so the next run can process it once
            # the factory is importable (docs/05 §2 rule 2). `event.skipped` says so unambiguously --
            # `event.failed` would imply an attempt was counted, and none was.
            context.logger.error(
                "event.skipped",
                extra={
                    "run_id": context.run.run_id,
                    "event_type": built.event.event_type,
                    "event_id": built.event.event_id,
                    "error_type": CONFIGURATION_FAILURE,
                },
                exc_info=built.error,
            )
            return _outcome(
                built,
                error_type=CONFIGURATION_FAILURE,
                error_message=summarise_exception(built.error),
            )

        classification = _classify_failure(built.error)
        reason = summarise_exception(built.error)
        if classification == UNCLASSIFIED_FAILURE:
            # The classification exists to stop impersonation, not to hide what actually failed, so the
            # real type stays visible in the outcome and the log.
            reason = f"{type(built.error).__name__}: {reason}"
        return _record_failure(
            built,
            store=store,
            context=context,
            classification=classification,
            reason=reason,
            error=built.error,
        )

    if failed_blocks and fail_on_block_error:
        shown = ", ".join(outcome.name for outcome in failed_blocks[:_REASON_NAMES])
        remainder = len(failed_blocks) - _REASON_NAMES
        more = f" and {remainder} more" if remainder > 0 else ""
        reason = f"{len(failed_blocks)} block(s) did not succeed: {shown}{more}"[:_REASON_LIMIT]
        return _record_failure(
            built,
            store=store,
            context=context,
            classification=BLOCK_FAILURE,
            reason=reason,
            error=None,
        )

    if built.output_uri is None:
        # Unreachable with a required writer, which always returns a location. Reported rather than
        # asserted so a malformed writer cannot crash the job at exit-code time.
        return _record_failure(
            built,
            store=store,
            context=context,
            classification=OUTPUT_FAILURE,
            reason="the writer returned no location for the report",
            error=None,
        )

    try:
        store.mark_processed(built.event, output_uri=built.output_uri)
    except Exception as exc:  # noqa: BLE001 - a store that raises anything has still failed
        return _store_failure(built, error=exc, context=context)

    context.logger.info(
        "event.processed",
        extra={
            "run_id": context.run.run_id,
            "event_type": built.event.event_type,
            "event_id": built.event.event_id,
            "output_uri": built.output_uri,
        },
    )
    return _outcome(built)


def _record_failure(
    built: _BuiltReport,
    *,
    store: EventStore,
    context: ReportContext,
    classification: str,
    reason: str,
    error: BaseException | None,
) -> EventProcessingOutcome:
    """Count the attempt, retire it if this was the last, and record the outcome."""
    try:
        record = store.mark_failed(built.event, reason=reason)
    except Exception as exc:  # noqa: BLE001 - a store that raises anything has still failed
        return _store_failure(built, error=exc, context=context)

    context.logger.error(
        "event.failed",
        extra={
            "run_id": context.run.run_id,
            "event_type": built.event.event_type,
            "event_id": built.event.event_id,
            "attempts": record.attempts,
            "reason": reason,
        },
        exc_info=error,
    )
    if record.retired:
        context.logger.warning(
            "event.retired",
            extra={
                "run_id": context.run.run_id,
                "event_type": built.event.event_type,
                "event_id": built.event.event_id,
                "attempts": record.attempts,
            },
        )
    return _outcome(built, error_type=classification, error_message=reason)


def _store_failure(
    built: _BuiltReport, *, error: Exception, context: ReportContext
) -> EventProcessingOutcome:
    context.logger.exception(
        "store.failed",
        extra={
            "run_id": context.run.run_id,
            "event_type": built.event.event_type,
            "event_id": built.event.event_id,
        },
    )
    return _outcome(
        built,
        error_type=STORE_FAILURE,
        error_message=summarise_exception(error),
    )


def _outcome(
    built: _BuiltReport,
    *,
    error_type: str | None = None,
    error_message: str | None = None,
) -> EventProcessingOutcome:
    return EventProcessingOutcome(
        event=built.event,
        report_title=built.title,
        rows=built.rows,
        output_uri=built.output_uri,
        error_type=error_type,
        error_message=error_message,
    )

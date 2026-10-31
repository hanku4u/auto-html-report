"""One implementation of the execution rules, shared by blocks and discoverers.

``docs/02-block-authoring.md`` §6 defines them for a block: a per-attempt budget measured on the
injected clock, a retry loop with exponential backoff, failure isolation, and a real-time backstop for
a worker that never returns (ADR-0027). ``docs/05-discovery-and-processing.md`` §1 says discoverers
are subject to the same rules, and that those mechanics are implemented **once**. This module is that
once.

Only the vocabulary differs. A block logs ``block.start`` and says that "``collect()`` exceeded its
budget"; a discoverer logs ``discovery.start`` and names ``discover()``. That difference is a frozen
:class:`ExecutionVocabulary` passed in, not a second copy of the timeout logic -- the cost of copying
it is that the copies drift, and the drift would show up as a retry policy that applies to one kind of
work and not the other.

**Two kinds of time, deliberately.** Retry backoff, per-attempt budgets and reported durations all run
on the injected ``Clock``, which makes them fully testable against a fake. The last-resort hang guard
does not: it waits on real time, because a deadline measured on a clock the wedged code controls is
not a hang guard at all (ADR-0027).

**One budget, both paths.** The per-attempt budget is checked whether the attempt returns or raises. An
attempt that spent its budget and then failed is a timeout, not a retryable error: retrying it would
overrun again, and reporting the eventual success would hide that the first attempt was unusable.

**Discoverers share the block timeout and retry settings.** ``docs/05`` grants them the same rules as
blocks and defines no separate knobs, so ``block_timeout_seconds`` and ``block_retries`` apply to both.
The names are historical; the semantics are "the default budget and retry count for one unit of
concurrent work".
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Generic, TypeVar, cast

from auto_html_report.application.ports import Clock
from auto_html_report.domain.errors import ConfigurationError, TransientBlockError
from auto_html_report.infrastructure.settings import MAX_BLOCK_RETRIES, Settings

#: Exceptions retried by default. Everything else is permanent: retrying a 404 or a malformed payload
#: three times is just a slower way to fail (``docs/02-block-authoring.md`` §6.3).
_DEFAULT_RETRYABLE: Final = (
    TransientBlockError,
    TimeoutError,
    ConnectionError,
)

#: Added to the computed budget of the hang guard, so that a task finishing just inside its last
#: attempt is not abandoned because the bookkeeping took a moment.
_BACKSTOP_GRACE_SECONDS: Final = 1.0

#: Error messages are for a report footer or a log line, not a novel: capped here and capped again by
#: the renderer for its summary view.
_MESSAGE_LIMIT: Final = 300

T = TypeVar("T")
R = TypeVar("R")


class ExecutionStatus(StrEnum):
    """How one task ended. ``FAILED`` and ``TIMED_OUT`` are isolated failures, not exceptions."""

    OK = "OK"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True, slots=True)
class ExecutionVocabulary:
    """The words one caller uses in its log lines and error messages.

    The mechanism below is shared; the vocabulary is the only thing that differs, and it is a
    parameter precisely so that adding a third kind of concurrent work does not copy the timeout
    logic with a different prefix.
    """

    #: Log event prefix: ``"block"`` produces ``block.start``, ``"discovery"`` produces
    #: ``discovery.start``.
    kind: str
    #: The field naming the task in a log line: ``"block"`` or ``"discoverer"``.
    subject: str
    #: What ran out of budget, as it appears in an error message: ``"collect()"`` or ``"discover()"``.
    stage: str


BLOCK_EXECUTION: Final = ExecutionVocabulary(kind="block", subject="block", stage="collect()")
DISCOVERY_EXECUTION: Final = ExecutionVocabulary(
    kind="discovery", subject="discoverer", stage="discover()"
)


@dataclass(frozen=True, slots=True)
class Task(Generic[T, R]):
    """One unit of concurrent work, and the limits it declares for itself.

    ``attempt`` is called once per attempt and may perform I/O. ``finalise`` -- the block's ``render()``
    -- runs in the same worker after a successful attempt, inside the hang guard but outside the
    per-attempt budget, and is **never retried**: it is pure, so a second call would fail identically.
    Discoverers pass ``None`` and their attempt's value is the result.

    ``timeout_seconds`` and ``max_retries`` override the configured defaults for this task; ``None``
    defers to them. A block sets them per instance, a discoverer does not (``docs/05`` §1).
    """

    name: str
    attempt: Callable[[int], T]
    finalise: Callable[[T, int], R] | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult(Generic[R]):
    """The outcome of one task. ``value`` is set only when ``status`` is ``OK``."""

    status: ExecutionStatus
    value: R | None
    attempts: int
    duration_seconds: float
    error_type: str | None = None
    error_message: str | None = None


class _Progress:
    """How many attempts a task has started, readable by the caller while the worker runs.

    The caller needs this when the hang guard fires: the worker is wedged inside its attempt, so only
    the worker knows whether that was the first attempt or a retry. ``attempts`` is documented as the
    attempts *made*, so reporting the allowance instead would put a fabricated number in a report --
    and on the path where diagnosis matters most.
    """

    def __init__(self) -> None:
        self._attempts = 0
        self._lock = threading.Lock()

    def start_attempt(self) -> int:
        """Record that an attempt is beginning, and return its number."""
        with self._lock:
            self._attempts += 1
            return self._attempts

    @property
    def started(self) -> int:
        """Attempts begun so far. Never zero once a worker has been submitted."""
        with self._lock:
            return max(1, self._attempts)


def execute_tasks(
    tasks: Sequence[Task[Any, Any]],
    *,
    settings: Settings,
    clock: Clock,
    logger: logging.Logger,
    run_id: str,
    vocabulary: ExecutionVocabulary,
    on_success: Callable[[str, ExecutionResult[Any]], None] | None = None,
) -> tuple[ExecutionResult[Any], ...]:
    """Run every task concurrently and return one result per task, in argument order.

    Never raises for a task-level failure: that task's result carries ``FAILED`` or ``TIMED_OUT`` and
    its error type and message. Raises :class:`ConfigurationError` for an invalid task set, because
    that is a programming error rather than a data condition.

    Logging is per task, at the moment it happens: start, retry, failure, abandonment. The success line
    belongs to the caller, because its fields differ -- a block reports ``block.finish`` with a status
    after rendering, a discoverer reports ``discovery.finish`` with what was persisted. ``on_success``
    runs in the worker, right after the result is computed, so that a finished block is visible while a
    slower sibling is still running rather than only when the whole batch is done.
    """
    _validate(tasks, settings)

    workers = max(1, min(settings.max_workers, len(tasks)))
    results: list[ExecutionResult[Any] | None] = [None] * len(tasks)
    progress = [_Progress() for _ in tasks]

    # Deliberately not a `with` block: on exit that waits for every worker, including one that has been
    # abandoned inside a wedged attempt. Abandoning means *we* stop waiting; the thread is not killable
    # and will finish when its own I/O does (ADR-0005).
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="auto-html-report")
    try:
        futures: list[Future[ExecutionResult[Any]]] = []
        submitted_at: list[float] = []
        for task, tracker in zip(tasks, progress, strict=True):
            futures.append(
                pool.submit(
                    _run_task,
                    task,
                    settings,
                    clock,
                    logger,
                    run_id,
                    vocabulary,
                    tracker,
                    on_success,
                )
            )
            # Real time, recorded per submission: the hang guard must be a deadline for the *run*,
            # anchored to when the task started, not a fresh wait re-armed for each future in turn.
            # Otherwise N wedged tasks cost N full guards, and a ten-block upstream outage takes half
            # an hour to produce ten timeout rows.
            submitted_at.append(time.monotonic())

        for index in range(len(tasks)):
            results[index] = _await_result(
                futures[index],
                tasks[index],
                settings,
                logger,
                run_id,
                vocabulary,
                progress[index],
                submitted_at[index],
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if any(result is None for result in results):
        # Every index is assigned by the loop above, so this is a bug in this module. Filtering the gap
        # out would silently shorten the tuple and break the one-result-per-task invariant.
        raise AssertionError("internal error: a task produced no result")

    return cast("tuple[ExecutionResult[Any], ...]", tuple(results))


def _await_result(
    future: Future[ExecutionResult[Any]],
    task: Task[Any, Any],
    settings: Settings,
    logger: logging.Logger,
    run_id: str,
    vocabulary: ExecutionVocabulary,
    tracker: _Progress,
    submitted_at: float,
) -> ExecutionResult[Any]:
    """Wait for one task, with a deadline anchored to its submission."""
    guard = _hang_guard_seconds(task, settings)
    remaining = max(0.0, guard - (time.monotonic() - submitted_at))

    try:
        return future.result(timeout=remaining)
    except FutureTimeoutError:
        logger.warning(
            f"{vocabulary.kind}.abandoned",
            extra={
                "run_id": run_id,
                vocabulary.subject: task.name,
                "overrun_s": guard,
                "attempts": tracker.started,
            },
        )
        return ExecutionResult(
            status=ExecutionStatus.TIMED_OUT,
            value=None,
            attempts=tracker.started,
            duration_seconds=guard,
            error_type="TimeoutError",
            error_message=f"{vocabulary.kind} did not finish within its {guard:.1f} s total budget",
        )
    except Exception as exc:
        # A failure raised outside the worker's own handler (a broken log handler, say). It still gets
        # the fields docs/07 §4 promises for `{kind}.failed`, so the contract does not depend on where
        # the failure happened.
        logger.exception(
            f"{vocabulary.kind}.failed",
            extra={
                "run_id": run_id,
                vocabulary.subject: task.name,
                "attempts": tracker.started,
                "error_type": type(exc).__name__,
                "error_message": summarise_exception(exc),
            },
        )
        return ExecutionResult(
            status=ExecutionStatus.FAILED,
            value=None,
            attempts=tracker.started,
            duration_seconds=guard - remaining,
            error_type=type(exc).__name__,
            error_message=summarise_exception(exc),
        )


def _run_task(
    task: Task[Any, Any],
    settings: Settings,
    clock: Clock,
    logger: logging.Logger,
    run_id: str,
    vocabulary: ExecutionVocabulary,
    tracker: _Progress,
    on_success: Callable[[str, ExecutionResult[Any]], None] | None,
) -> ExecutionResult[Any]:
    """One task: attempts, budget checks, optional finalise. Never raises for a task failure.

    ``Exception`` rather than ``BaseException``: process-control exceptions (``SystemExit``,
    ``KeyboardInterrupt``) are not task failures and must unwind, not become a red cell.
    """
    attempts_allowed = _attempts_allowed(task, settings)
    attempt_budget = _attempt_budget(task, settings)
    started = clock.monotonic()

    for attempt in range(1, attempts_allowed + 1):
        tracker.start_attempt()
        logger.info(
            f"{vocabulary.kind}.start",
            extra={"run_id": run_id, vocabulary.subject: task.name, "attempt": attempt},
        )
        attempt_started = clock.monotonic()

        try:
            value = task.attempt(attempt)
        except Exception as exc:
            # The budget is checked here too, not only on the success path: an attempt that spent its
            # budget and then failed must not be retried, or a fast retry would report the task healthy
            # despite its first attempt having been unusable (02 §6.2).
            consumed = clock.monotonic() - attempt_started
            if consumed > attempt_budget:
                return _timed_out(
                    task, attempt, attempt_budget, consumed, logger, run_id, vocabulary
                )

            if _is_retryable(exc, settings) and attempt < attempts_allowed:
                delay = _backoff_seconds(attempt, settings)
                logger.warning(
                    f"{vocabulary.kind}.retry",
                    extra={
                        "run_id": run_id,
                        vocabulary.subject: task.name,
                        "attempt": attempt,
                        "delay_s": delay,
                        "error_type": type(exc).__name__,
                    },
                )
                clock.sleep(delay)
                continue

            logger.exception(
                f"{vocabulary.kind}.failed",
                extra={
                    "run_id": run_id,
                    vocabulary.subject: task.name,
                    "attempts": attempt,
                    "error_type": type(exc).__name__,
                    "error_message": summarise_exception(exc),
                },
            )
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                value=None,
                attempts=attempt,
                duration_seconds=clock.monotonic() - started,
                error_type=type(exc).__name__,
                error_message=summarise_exception(exc),
            )

        consumed = clock.monotonic() - attempt_started
        if consumed > attempt_budget:
            return _timed_out(task, attempt, attempt_budget, consumed, logger, run_id, vocabulary)

        if task.finalise is not None:
            try:
                final_value: Any = task.finalise(value, attempt)
            except Exception as exc:
                # A finalise failure is permanent by construction: the step is pure, so another attempt
                # would fail the same way. It is reported like any other failure, never retried.
                logger.exception(
                    f"{vocabulary.kind}.failed",
                    extra={
                        "run_id": run_id,
                        vocabulary.subject: task.name,
                        "attempts": attempt,
                        "error_type": type(exc).__name__,
                        "error_message": summarise_exception(exc),
                    },
                )
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    value=None,
                    attempts=attempt,
                    duration_seconds=clock.monotonic() - started,
                    error_type=type(exc).__name__,
                    error_message=summarise_exception(exc),
                )
        else:
            # With no finaliser the attempt's value *is* the result; the caller chose R = T.
            final_value = cast("Any", value)

        result = ExecutionResult(
            status=ExecutionStatus.OK,
            value=final_value,
            attempts=attempt,
            duration_seconds=clock.monotonic() - started,
        )
        if on_success is not None:
            # In the worker, so the success line is timely. The callback only logs; it must not raise.
            on_success(task.name, result)
        return result

    # Unreachable: the loop always returns on its last attempt.
    raise AssertionError(f"task {task.name!r} exhausted its attempts without a result")


def _timed_out(
    task: Task[Any, Any],
    attempt: int,
    attempt_budget: float,
    consumed: float,
    logger: logging.Logger,
    run_id: str,
    vocabulary: ExecutionVocabulary,
) -> ExecutionResult[Any]:
    """The one timeout result, used from both the success and the failure path so they cannot drift."""
    message = f"{vocabulary.stage} exceeded its {attempt_budget:.1f} s budget"
    logger.warning(
        f"{vocabulary.kind}.failed",
        extra={
            "run_id": run_id,
            vocabulary.subject: task.name,
            "attempts": attempt,
            "error_type": "TimeoutError",
            "error_message": message,
        },
    )
    return ExecutionResult(
        status=ExecutionStatus.TIMED_OUT,
        value=None,
        attempts=attempt,
        duration_seconds=consumed,
        error_type="TimeoutError",
        error_message=message,
    )


def _validate(tasks: Sequence[Task[Any, Any]], settings: Settings) -> None:
    """Reject task sets and overrides that cannot produce a sensible outcome."""
    if not tasks:
        raise ConfigurationError("a run must contain at least one task")

    for task in tasks:
        if task.timeout_seconds is not None and task.timeout_seconds <= 0:
            # A negative budget makes every attempt instantly TIMED_OUT, which looks like a broken task
            # rather than a broken declaration. Zero is rejected too: "no budget" is not a thing.
            raise ConfigurationError(
                f"{task.name!r} declares timeout_seconds={task.timeout_seconds!r}; "
                "it must be positive"
            )
        if task.max_retries is not None and task.max_retries < 0:
            raise ConfigurationError(
                f"{task.name!r} declares max_retries={task.max_retries!r}; it must not be negative"
            )
        if task.max_retries is not None and task.max_retries > MAX_BLOCK_RETRIES:
            # Exponential backoff: a huge retry count overflows the hang-guard arithmetic before any
            # work starts, which would be a crash from a configuration value.
            raise ConfigurationError(
                f"{task.name!r} declares max_retries={task.max_retries!r}; it must be at most "
                f"{MAX_BLOCK_RETRIES} (exponential backoff overflows the hang-guard arithmetic)"
            )

    if settings.block_retries > MAX_BLOCK_RETRIES:
        raise ConfigurationError(
            f"block_retries is {settings.block_retries!r}; it must be at most {MAX_BLOCK_RETRIES} "
            "(exponential backoff overflows the hang-guard arithmetic)"
        )

    if settings.block_timeout_seconds <= 0:
        raise ConfigurationError(
            f"block_timeout_seconds is {settings.block_timeout_seconds!r}; it must be positive"
        )


def _attempts_allowed(task: Task[Any, Any], settings: Settings) -> int:
    """Total attempts, first included: the task's own override or the configured retries."""
    retries = task.max_retries if task.max_retries is not None else settings.block_retries
    return max(1, 1 + retries)


def _attempt_budget(task: Task[Any, Any], settings: Settings) -> float:
    """Per-attempt budget: the task's own override or the configured default."""
    return task.timeout_seconds or settings.block_timeout_seconds


def _backoff_seconds(attempt: int, settings: Settings) -> float:
    """Exponential backoff with jitter, as documented: base * 2**(attempt-1), plus up to base again."""
    base = settings.retry_base_delay_seconds
    # 2.0 rather than 2, so the exponentiation is typed float: mypy treats int ** int as Any because a
    # negative exponent would make it a float, and Any here silently weakens the function's signature.
    return base * 2.0 ** (attempt - 1) + random.uniform(0, base)


def _hang_guard_seconds(task: Task[Any, Any], settings: Settings) -> float:
    """The real-time deadline for one task: every attempt's budget, the worst-case backoff, and grace.

    Computed as an upper bound, because the actual jitter is random. Real seconds, deliberately -- see
    the module docstring and ADR-0027.
    """
    attempts = _attempts_allowed(task, settings)
    attempt_budget = _attempt_budget(task, settings)
    base = settings.retry_base_delay_seconds
    worst_backoff = sum(base * 2.0 ** (attempt - 1) + base for attempt in range(1, attempts))
    return attempt_budget * attempts + worst_backoff + _BACKSTOP_GRACE_SECONDS


def _is_retryable(exc: BaseException, settings: Settings) -> bool:
    """Whether the retry policy should try again: the defaults, plus any configured class names."""
    if isinstance(exc, _DEFAULT_RETRYABLE):
        return True
    configured = set(settings.retryable_exceptions)
    return any(cls.__name__ in configured for cls in type(exc).__mro__)


def summarise_exception(exc: BaseException) -> str:
    """The message, newlines included, capped by length.

    Package-internal but not private: the processing job records the same capped text in an
    ``EventProcessingOutcome``, and two caps with different limits would be a bug nobody notices.

    Not the first line: the renderer decides whether to show the first line (``summary``) or all of it
    (``detailed``), per ``docs/06-output-html.md`` §6. Collapsing to one line here would silently make
    the detailed view identical to the summary, which is exactly what the setting exists to distinguish.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    return text[:_MESSAGE_LIMIT]

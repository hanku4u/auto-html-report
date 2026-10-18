"""Concurrent collection: every block in a report, assembled into ordered rows.

Implements the execution rules in ``docs/02-block-authoring.md`` §6. Three things about this module are
worth knowing before reading it:

**Collection produces data and outcomes; rendering produces HTML.** A failed block's row carries an empty
``html`` and a populated ``outcome``, and the error cell is built by the renderer from that outcome. That
is not an accident of layering (application/ may not import rendering/) — it is the right split: this
module knows *what happened*, the renderer knows *how it looks*, and the error detail level is a
presentation setting.

**Two kinds of time, deliberately.** Retry backoff, per-attempt budgets and reported durations all run on
the injected ``Clock``, which makes them fully testable against a fake. The last-resort hang guard does
not: it waits on real time, because a deadline measured on a clock the wedged code controls is not a hang
guard at all — a block stuck inside ``collect()`` never advances it. See ADR-0027.

**One budget, both paths.** The per-attempt budget is checked whether ``collect()`` returns or raises. An
attempt that spent its budget and then failed is a timeout, not a retryable error: retrying it would
overrun again, and reporting the eventual success would hide that the first attempt was unusable.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, cast

from auto_inform.application.context import RenderContext, ReportContext
from auto_inform.domain.block import Block, BlockOutcome, BlockStatus
from auto_inform.domain.errors import ConfigurationError, TransientBlockError
from auto_inform.domain.report import ReportRow
from auto_inform.infrastructure.settings import Settings

#: Exceptions retried by default. Everything else is permanent: retrying a 404 or a malformed payload
#: three times is just a slower way to fail (`docs/02-block-authoring.md` §6.3).
_DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    TransientBlockError,
    TimeoutError,
    ConnectionError,
)

#: Added to the computed budget of the hang guard, so that a block finishing just inside its last attempt
#: is not abandoned because the bookkeeping took a moment.
_BACKSTOP_GRACE_SECONDS = 1.0

#: Error messages are for a report footer, not a log file: first line, capped here and capped again by
#: the renderer for the summary view.
_MESSAGE_LIMIT = 300


class _Progress:
    """How many attempts a block has started, readable by the caller while the worker runs.

    The caller needs this when the hang guard fires: the worker is wedged inside ``collect()``, so only the
    worker knows whether that was the first attempt or a retry. ``BlockOutcome.attempts`` is documented as
    the attempts *made*, so reporting the allowance instead would put a fabricated number in a report —
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


def collect_blocks(
    blocks: Sequence[Block[Any]],
    *,
    context: ReportContext,
    report_title: str = "",
) -> tuple[ReportRow, ...]:
    """Collect and render every block concurrently; return rows in argument order.

    Never raises for a block-level failure: such a row carries a ``FAILED`` or ``TIMED_OUT`` outcome and
    an empty ``html``, which the renderer turns into an error cell. Raises :class:`ConfigurationError`
    for an invalid block set, because that is a programming error rather than a data condition.

    ``report_title`` is offered to blocks through their ``RenderContext``. It defaults to empty because
    collection is also usable outside a report definition, but a report should always pass it.
    """
    _validate(blocks, context.settings)

    render_context = RenderContext(generated_at=context.clock.now(), report_title=report_title)
    workers = max(1, min(context.settings.max_workers, len(blocks)))
    rows: list[ReportRow | None] = [None] * len(blocks)
    progress = [_Progress() for _ in blocks]

    # Deliberately not a `with` block: on exit that waits for every worker, including one that has been
    # abandoned inside a wedged collect(). Abandoning means *we* stop waiting; the thread is not killable
    # and will finish when its own I/O does (ADR-0005).
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="auto-inform")
    try:
        futures: list[Future[ReportRow]] = []
        submitted_at: list[float] = []
        for block, tracker in zip(blocks, progress, strict=True):
            futures.append(pool.submit(_collect_one, block, context, render_context, tracker))
            # Real time, recorded per submission: the hang guard must be a deadline for the *run*, anchored
            # to when the block started, not a fresh wait re-armed for each future in turn. Otherwise N
            # wedged blocks cost N full guards, and a ten-block upstream outage takes half an hour to
            # produce ten timeout rows.
            submitted_at.append(time.monotonic())

        for index in range(len(blocks)):
            rows[index] = _await_row(
                futures[index], blocks[index], context, progress[index], submitted_at[index]
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if any(row is None for row in rows):
        # Every index is assigned by the loop above, so this is a bug in this module. Filtering the gap
        # out would silently shorten the tuple and break the one-row-per-block invariant.
        raise AssertionError("internal error: a block produced no row")

    return cast("tuple[ReportRow, ...]", tuple(rows))


def _await_row(
    future: Future[ReportRow],
    block: Block[Any],
    context: ReportContext,
    tracker: _Progress,
    submitted_at: float,
) -> ReportRow:
    """Wait for one block, with a deadline anchored to its submission."""
    guard = _hang_guard_seconds(block, context)
    remaining = max(0.0, guard - (time.monotonic() - submitted_at))

    try:
        return future.result(timeout=remaining)
    except FutureTimeoutError:
        context.logger.warning(
            "block.abandoned",
            extra={
                "run_id": context.run.run_id,
                "block": block.name,
                "overrun_s": guard,
                "attempts": tracker.started,
            },
        )
        return _row(
            block,
            BlockStatus.TIMED_OUT,
            attempts=tracker.started,
            duration_seconds=guard,
            error_type="TimeoutError",
            error_message=f"block did not finish within its {guard:.1f} s total budget",
        )
    except Exception as exc:
        context.logger.exception(
            "block.failed",
            extra={
                "run_id": context.run.run_id,
                "block": block.name,
                "error_type": type(exc).__name__,
            },
        )
        return _row(
            block,
            BlockStatus.FAILED,
            attempts=tracker.started,
            duration_seconds=guard - remaining,
            error_type=type(exc).__name__,
            error_message=_summarise(exc),
        )


def _collect_one(
    block: Block[Any],
    context: ReportContext,
    render_context: RenderContext,
    tracker: _Progress,
) -> ReportRow:
    """One block: attempts, budget checks, render. Never raises for a block failure.

    ``Exception`` rather than ``BaseException``: process-control exceptions (``SystemExit``,
    ``KeyboardInterrupt``) are not block failures and must unwind, not become a red cell.
    """
    settings = context.settings
    attempts_allowed = _attempts_allowed(block, settings)
    attempt_budget = _attempt_budget(block, settings)
    started = context.clock.monotonic()

    for attempt in range(1, attempts_allowed + 1):
        tracker.start_attempt()
        context.logger.info(
            "block.start",
            extra={"run_id": context.run.run_id, "block": block.name, "attempt": attempt},
        )
        attempt_started = context.clock.monotonic()

        try:
            data = block.collect(context)
        except Exception as exc:
            # The budget is checked here too, not only on the success path: an attempt that spent its
            # budget and then failed must not be retried, or a fast retry would report the block healthy
            # despite its first attempt having been unusable (02 §6.2).
            consumed = context.clock.monotonic() - attempt_started
            if consumed > attempt_budget:
                return _timed_out_row(block, attempt, attempt_budget, consumed, context)

            if _is_retryable(exc, settings) and attempt < attempts_allowed:
                delay = _backoff_seconds(attempt, settings)
                context.logger.warning(
                    "block.retry",
                    extra={
                        "run_id": context.run.run_id,
                        "block": block.name,
                        "attempt": attempt,
                        "delay_s": delay,
                        "error_type": type(exc).__name__,
                    },
                )
                context.clock.sleep(delay)
                continue

            context.logger.exception(
                "block.failed",
                extra={
                    "run_id": context.run.run_id,
                    "block": block.name,
                    "attempts": attempt,
                    "error_type": type(exc).__name__,
                    "error_message": _summarise(exc),
                },
            )
            return _row(
                block,
                BlockStatus.FAILED,
                attempts=attempt,
                duration_seconds=context.clock.monotonic() - started,
                error_type=type(exc).__name__,
                error_message=_summarise(exc),
            )

        consumed = context.clock.monotonic() - attempt_started
        if consumed > attempt_budget:
            return _timed_out_row(block, attempt, attempt_budget, consumed, context)

        try:
            html = block.render(data, render_context)
        except Exception as exc:
            context.logger.exception(
                "block.failed",
                extra={
                    "run_id": context.run.run_id,
                    "block": block.name,
                    "attempts": attempt,
                    "error_type": type(exc).__name__,
                    "error_message": _summarise(exc),
                },
            )
            return _row(
                block,
                BlockStatus.FAILED,
                attempts=attempt,
                duration_seconds=context.clock.monotonic() - started,
                error_type=type(exc).__name__,
                error_message=_summarise(exc),
            )

        outcome = BlockOutcome(
            name=block.name,
            status=BlockStatus.OK,
            duration_seconds=context.clock.monotonic() - started,
            attempts=attempt,
        )
        context.logger.info(
            "block.finish",
            extra={
                "run_id": context.run.run_id,
                "block": block.name,
                "status": outcome.status.value,
                "duration_ms": round(outcome.duration_seconds * 1000, 1),
                "attempts": attempt,
            },
        )
        return ReportRow(item=block.name, html=html, outcome=outcome)

    # Unreachable: the loop always returns on its last attempt.
    raise AssertionError(f"block {block.name!r} exhausted its attempts without an outcome")


def _timed_out_row(
    block: Block[Any],
    attempt: int,
    attempt_budget: float,
    consumed: float,
    context: ReportContext,
) -> ReportRow:
    """The one timeout row, used from both the success and the failure path so they cannot drift."""
    message = f"collect() exceeded its {attempt_budget:.1f} s budget"
    context.logger.warning(
        "block.failed",
        extra={
            "run_id": context.run.run_id,
            "block": block.name,
            "attempts": attempt,
            "error_type": "TimeoutError",
            "error_message": message,
        },
    )
    return _row(
        block,
        BlockStatus.TIMED_OUT,
        attempts=attempt,
        duration_seconds=consumed,
        error_type="TimeoutError",
        error_message=message,
    )


def _row(
    block: Block[Any],
    status: BlockStatus,
    *,
    attempts: int,
    duration_seconds: float,
    error_type: str | None = None,
    error_message: str | None = None,
) -> ReportRow:
    """A row for a block that did not succeed. The renderer builds the cell from the outcome."""
    return ReportRow(
        item=block.name,
        html="",
        outcome=BlockOutcome(
            name=block.name,
            status=status,
            duration_seconds=duration_seconds,
            attempts=attempts,
            error_type=error_type,
            error_message=error_message,
        ),
    )


def _validate(blocks: Sequence[Block[Any]], settings: Settings) -> None:
    """Reject the block sets and overrides that cannot produce a sensible report."""
    if not blocks:
        raise ConfigurationError("a report must contain at least one block")

    seen: set[str] = set()
    duplicates: set[str] = set()
    for block in blocks:
        if block.name in seen:
            duplicates.add(block.name)
        seen.add(block.name)

    if duplicates:
        raise ConfigurationError(
            f"duplicate block names: {sorted(duplicates)}. Names fill the Item column, so a reader "
            "cannot tell duplicate rows apart; pass name=... to distinguish repeated blocks."
        )

    for block in blocks:
        if block.timeout_seconds is not None and block.timeout_seconds <= 0:
            # A negative budget makes every attempt instantly TIMED_OUT, which looks like a broken block
            # rather than a broken declaration. Zero is rejected too: "no budget" is not a thing.
            raise ConfigurationError(
                f"{block.name!r} declares timeout_seconds={block.timeout_seconds!r}; it must be positive"
            )
        if block.max_retries is not None and block.max_retries < 0:
            raise ConfigurationError(
                f"{block.name!r} declares max_retries={block.max_retries!r}; it must not be negative"
            )

    if settings.block_timeout_seconds <= 0:
        raise ConfigurationError(
            f"block_timeout_seconds is {settings.block_timeout_seconds!r}; it must be positive"
        )


def _attempts_allowed(block: Block[Any], settings: Settings) -> int:
    """Total attempts, first included: the block's own override or the configured retries."""
    retries = block.max_retries if block.max_retries is not None else settings.block_retries
    return max(1, 1 + retries)


def _attempt_budget(block: Block[Any], settings: Settings) -> float:
    """Per-attempt budget: the block's own override or the configured default."""
    return block.timeout_seconds or settings.block_timeout_seconds


def _backoff_seconds(attempt: int, settings: Settings) -> float:
    """Exponential backoff with jitter, as documented: base * 2**(attempt-1), plus up to base again."""
    base = settings.retry_base_delay_seconds
    # 2.0 rather than 2, so the exponentiation is typed float: mypy treats int ** int as Any because a
    # negative exponent would make it a float, and Any here silently weakens the function's signature.
    return base * 2.0 ** (attempt - 1) + random.uniform(0, base)


def _hang_guard_seconds(block: Block[Any], context: ReportContext) -> float:
    """The real-time deadline for one block: every attempt's budget, the worst-case backoff, and grace.

    Computed as an upper bound, because the actual jitter is random. Real seconds, deliberately -- see
    the module docstring and ADR-0027.
    """
    settings = context.settings
    attempts = _attempts_allowed(block, settings)
    attempt_budget = _attempt_budget(block, settings)
    base = settings.retry_base_delay_seconds
    worst_backoff = sum(base * 2.0 ** (attempt - 1) + base for attempt in range(1, attempts))
    return attempt_budget * attempts + worst_backoff + _BACKSTOP_GRACE_SECONDS


def _is_retryable(exc: BaseException, settings: Settings) -> bool:
    """Whether the retry policy should try again: the defaults, plus any configured class names."""
    if isinstance(exc, _DEFAULT_RETRYABLE):
        return True
    configured = set(settings.retryable_exceptions)
    return any(cls.__name__ in configured for cls in type(exc).__mro__)


def _summarise(exc: BaseException) -> str:
    """The message, newlines included, capped by length.

    Not the first line: the renderer decides whether to show the first line (``summary``) or all of it
    (``detailed``), per ``docs/06-output-html.md`` §6. Collapsing to one line here would silently make the
    detailed view identical to the summary, which is exactly what the setting exists to distinguish.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    return text[:_MESSAGE_LIMIT]

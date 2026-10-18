"""Concurrent collection: every block in a report, assembled into ordered rows.

Implements the execution rules in ``docs/02-block-authoring.md`` §6. Two things about this module are
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
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

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
    _validate(blocks)

    render_context = RenderContext(
        generated_at=context.clock.now(),
        report_title=report_title,
    )
    workers = max(1, min(context.settings.max_workers, len(blocks)))
    rows: list[ReportRow | None] = [None] * len(blocks)

    # Deliberately not a `with` block: on exit that waits for every worker, including one that has been
    # abandoned inside a wedged collect(). Abandoning means *we* stop waiting; the thread is not killable
    # and will finish when its own I/O does (ADR-0005).
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="auto-inform")
    try:
        futures = [pool.submit(_collect_one, block, context, render_context) for block in blocks]
        for index, (block, future) in enumerate(zip(blocks, futures, strict=True)):
            rows[index] = _await_row(future, block, context)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return tuple(row for row in rows if row is not None)


def _await_row(future: Future[ReportRow], block: Block[Any], context: ReportContext) -> ReportRow:
    """Wait for one block, with a real-time hang guard as the last resort."""
    budget = _hang_guard_seconds(block, context)
    try:
        return future.result(timeout=budget)
    except FutureTimeoutError:
        context.logger.warning(
            "block.abandoned",
            extra={
                "run_id": context.run.run_id,
                "block": block.name,
                "overrun_s": budget,
            },
        )
        return _row(
            block,
            BlockStatus.TIMED_OUT,
            attempts=_attempts_allowed(block, context.settings),
            duration_seconds=budget,
            error_type="TimeoutError",
            error_message=f"block did not finish within its {budget:.1f} s total budget",
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
            attempts=1,
            duration_seconds=0.0,
            error_type=type(exc).__name__,
            error_message=_summarise(exc),
        )


def _collect_one(
    block: Block[Any], context: ReportContext, render_context: RenderContext
) -> ReportRow:
    """One block: attempts, budget checks, render. Never raises for a block failure."""
    settings = context.settings
    attempts_allowed = _attempts_allowed(block, settings)
    attempt_budget = _attempt_budget(block, settings)
    started = context.clock.monotonic()

    for attempt in range(1, attempts_allowed + 1):
        context.logger.info(
            "block.start",
            extra={"run_id": context.run.run_id, "block": block.name, "attempt": attempt},
        )
        attempt_started = context.clock.monotonic()

        try:
            data = block.collect(context)
        except BaseException as exc:
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
            # The attempt came back, but only after its budget. The work is unusable under the contract
            # and another attempt would overrun again, so no retry: report a timeout.
            context.logger.warning(
                "block.failed",
                extra={
                    "run_id": context.run.run_id,
                    "block": block.name,
                    "attempts": attempt,
                    "error_type": "TimeoutError",
                    "error_message": f"collect() exceeded its {attempt_budget:.1f} s budget",
                },
            )
            return _row(
                block,
                BlockStatus.TIMED_OUT,
                attempts=attempt,
                duration_seconds=consumed,
                error_type="TimeoutError",
                error_message=f"collect() exceeded its {attempt_budget:.1f} s budget",
            )

        try:
            html = block.render(data, render_context)
        except BaseException as exc:
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


def _validate(blocks: Sequence[Block[Any]]) -> None:
    """Reject the block sets that cannot produce a sensible report, the way a definition does."""
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
    """The first line of the message, capped. A traceback belongs in the log, never in a report."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:_MESSAGE_LIMIT]

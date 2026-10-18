"""Concurrent collection: every block in a report, assembled into ordered rows.

**Collection produces data and outcomes; rendering produces HTML.** A failed block's row carries an
empty ``html`` and a populated ``outcome``, and the error cell is built by the renderer from that
outcome. That is not an accident of layering (application/ may not import rendering/) — it is the right
split: this module knows *what happened*, the renderer knows *how it looks*, and the error detail level
is a presentation setting.

**The execution rules are not here.** Timeout accounting, the retry loop, backoff and the hang guard
live in ``application/execution.py``, because discoverers obey the same rules (``docs/05`` §1) and two
copies of that logic would drift. This module supplies the block-specific parts: which callables to
run, the duplicate-name check, and the mapping from an execution result to a :class:`ReportRow`. The
one thing it keeps in the worker is the success log line, so ``block.finish`` is emitted when the block
finishes rather than when the slowest sibling does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from auto_inform.application.context import RenderContext, ReportContext
from auto_inform.application.execution import (
    BLOCK_EXECUTION,
    ExecutionResult,
    ExecutionStatus,
    Task,
    execute_tasks,
)
from auto_inform.domain.block import Block, BlockOutcome, BlockStatus
from auto_inform.domain.errors import ConfigurationError
from auto_inform.domain.report import ReportRow

#: Execution results speak about tasks; a report speaks about blocks. One mapping, so the two cannot
#: drift and neither module has to know the other's enum.
_STATUS: dict[ExecutionStatus, BlockStatus] = {
    ExecutionStatus.OK: BlockStatus.OK,
    ExecutionStatus.FAILED: BlockStatus.FAILED,
    ExecutionStatus.TIMED_OUT: BlockStatus.TIMED_OUT,
}


def collect_blocks(
    blocks: Sequence[Block[Any]],
    *,
    context: ReportContext,
    report_title: str = "",
    generated_at: datetime | None = None,
) -> tuple[ReportRow, ...]:
    """Collect and render every block concurrently; return rows in argument order.

    Never raises for a block-level failure: such a row carries a ``FAILED`` or ``TIMED_OUT`` outcome and
    an empty ``html``, which the renderer turns into an error cell. Raises :class:`ConfigurationError`
    for an invalid block set, because that is a programming error rather than a data condition.

    ``report_title`` is offered to blocks through their ``RenderContext``. It defaults to empty because
    collection is also usable outside a report definition, but a report should always pass it.

    ``generated_at`` is the instant the report is stamped with, and it reaches every block's
    ``RenderContext``. Pass the same value the :class:`Report` will carry so that a block rendering it
    agrees with the document header; it defaults to the clock's current reading for standalone use.
    """
    _validate(blocks)

    render_context = RenderContext(
        generated_at=generated_at if generated_at is not None else context.clock.now(),
        report_title=report_title,
    )
    results = execute_tasks(
        [_task(block, context, render_context) for block in blocks],
        settings=context.settings,
        clock=context.clock,
        logger=context.logger,
        run_id=context.run.run_id,
        vocabulary=BLOCK_EXECUTION,
        on_success=_finish_logger(context),
    )
    return tuple(_row(block, result) for block, result in zip(blocks, results, strict=True))


def _task(
    block: Block[Any], context: ReportContext, render_context: RenderContext
) -> Task[Any, str]:
    """One block as a task: ``collect()`` per attempt, ``render()`` once on success.

    Named closures rather than lambdas: mypy cannot infer the callable types through ``Task``'s two type
    parameters, and a function with annotations is clearer than a lambda with a cast. Each call creates
    its own closures over ``block``, so the late-binding trap a comprehension would set does not exist.
    """

    def attempt(_attempt_number: int) -> Any:
        return block.collect(context)

    def finalise(data: Any, _attempt_number: int) -> str:
        return block.render(data, render_context)

    return Task(
        name=block.name,
        attempt=attempt,
        finalise=finalise,
        timeout_seconds=block.timeout_seconds,
        max_retries=block.max_retries,
    )


def _finish_logger(
    context: ReportContext,
) -> Callable[[str, ExecutionResult[Any]], None]:
    """The success log line, emitted from the worker the moment a block finishes."""

    def log_finish(name: str, result: ExecutionResult[Any]) -> None:
        context.logger.info(
            "block.finish",
            extra={
                "run_id": context.run.run_id,
                "block": name,
                "status": BlockStatus.OK.value,
                "duration_ms": round(result.duration_seconds * 1000, 1),
                "attempts": result.attempts,
            },
        )

    return log_finish


def _row(block: Block[Any], result: ExecutionResult[Any]) -> ReportRow:
    """One execution result as a row. A failed block's cell is the renderer's job."""
    outcome = BlockOutcome(
        name=block.name,
        status=_STATUS[result.status],
        duration_seconds=result.duration_seconds,
        attempts=result.attempts,
        error_type=result.error_type,
        error_message=result.error_message,
    )
    html = result.value if result.value is not None else ""
    return ReportRow(item=block.name, html=html, outcome=outcome)


def _validate(blocks: Sequence[Block[Any]]) -> None:
    """Reject the block sets that cannot produce a sensible report.

    The per-block overrides (``timeout_seconds``, ``max_retries``) and the settings bounds are checked
    by :func:`~auto_inform.application.execution.execute_tasks`, which is where those values are used.
    """
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

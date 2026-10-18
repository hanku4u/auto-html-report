"""Builders for hand-made domain objects.

Rendering, collection and the job runners all need a :class:`~auto_inform.domain.report.Report`
without touching the network, the clock or the filesystem. Those builders live here rather than being
re-declared in every test module.

Nothing in this module performs I/O, and nothing in it reads the real clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from auto_inform.application.context import RenderContext, ReportContext
from auto_inform.domain.block import Block, BlockOutcome, BlockStatus
from auto_inform.domain.report import Report, ReportDefinition, ReportRow

#: Fixed so that golden files are worth committing: a run id and a timestamp that never move.
FIXED_RUN_ID = "9f2c1ab34de5"
FIXED_GENERATED_AT = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


class StubBlock(Block[int]):
    """A block that does nothing, for building valid report definitions."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name)

    def collect(self, context: ReportContext) -> int:
        return 0

    def render(self, data: int, context: RenderContext) -> str:
        return ""


def make_outcome(
    name: str,
    status: BlockStatus = BlockStatus.OK,
    *,
    duration_seconds: float = 0.1,
    attempts: int = 1,
    error_type: str | None = None,
    error_message: str | None = None,
) -> BlockOutcome:
    """A :class:`BlockOutcome` with sensible defaults, overridable field by field."""
    return BlockOutcome(
        name=name,
        status=status,
        duration_seconds=duration_seconds,
        attempts=attempts,
        error_type=error_type,
        error_message=error_message,
    )


def make_row(
    item: str,
    html: str = "<p>stub</p>",
    *,
    status: BlockStatus = BlockStatus.OK,
    **outcome_fields: object,
) -> ReportRow:
    """A :class:`ReportRow` whose Item text is also its outcome's block name.

    ``html`` is the block's own markup and is ignored for a failed row: the renderer builds the error
    cell from the outcome, so a fixture that pre-builds one would be describing a pipeline that no
    longer exists.
    """
    return ReportRow(
        item=item,
        html=html,
        outcome=make_outcome(item, status, **outcome_fields),  # type: ignore[arg-type]
    )


def make_report(
    *rows: ReportRow,
    title: str = "Test report",
    subtitle: str | None = None,
    metadata: Mapping[str, str] | None = None,
    generated_at: datetime = FIXED_GENERATED_AT,
    run_id: str = FIXED_RUN_ID,
) -> Report:
    """A consistent :class:`Report`: one block per row, in the order given.

    Item texts must be unique, because a definition rejects duplicate block names -- which is the
    behaviour under test elsewhere, not something to work around here.
    """
    definition = ReportDefinition(
        title=title,
        subtitle=subtitle,
        blocks=[StubBlock(row.item) for row in rows],
        metadata=dict(metadata or {}),
    )
    return Report(
        definition=definition,
        rows=tuple(rows),
        generated_at=generated_at,
        run_id=run_id,
    )

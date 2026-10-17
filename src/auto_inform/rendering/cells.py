"""Row and cell markup.

Two responsibilities, deliberately separated by who owns them:

* :func:`build_row_views` turns assembled :class:`~auto_inform.domain.report.ReportRow` objects into
  the flat view models the template consumes. The template therefore stays dumb -- no attribute
  reaches into a domain object, and nothing in it calls ``strftime``.
* :func:`error_cell_html` builds the inner HTML of a failed row's Data cell. It lives here, next to
  the markup it must match, but it is *called* by collection (stage 5), not by rendering: a row's
  ``html`` already holds the error cell by the time a :class:`Report` exists
  (``docs/03-report-api.md`` §2). Keeping the builder here means the error markup has one home.

Everything in this module escapes what it interpolates. That is not optional: block HTML lands in the
document through the template's single ``|safe`` filter, so framework text placed alongside it has no
second line of defence. See ``docs/06-output-html.md`` §4 and §6.4.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from auto_inform._naming import slug
from auto_inform.domain.block import BlockOutcome, BlockStatus
from auto_inform.domain.report import Report, ReportRow
from auto_inform.rendering.escaping import escape

ErrorDetail = Literal["summary", "detailed"]

#: How much of an error message the summary view shows. Long enough to be actionable, short enough
#: not to turn one cell into a wall of text.
_SUMMARY_MAX_CHARS = 160


@dataclass(frozen=True, slots=True)
class RowView:
    """One row, flattened for the template.

    ``item`` is *not* escaped here: the template interpolates it normally and its ``autoescape`` does
    the work. ``html`` is trusted and is inserted with ``|safe``.
    """

    item: str
    html: str
    is_error: bool
    block_id: str


def build_row_views(rows: Sequence[ReportRow]) -> tuple[RowView, ...]:
    """Flatten rows for the template, preserving order.

    ``block_id`` is the slug of the row's Item text rather than of its block type. Two instances of
    one block type routinely appear in the same report -- "Maintenance -- T-104" and "Maintenance --
    T-112" -- so a type-derived id would collide, and uniqueness is the property that matters for the
    ``(run_id, block)`` key a future sign-off store would need (ADR-0018).
    """
    return tuple(
        RowView(
            item=row.item,
            html=row.html,
            is_error=row.outcome.status is not BlockStatus.OK,
            block_id=slug(row.item, fallback="block"),
        )
        for row in rows
    )


def build_report_rows(report: Report) -> tuple[RowView, ...]:
    """Convenience for the renderer: the view rows of a whole report."""
    return build_row_views(report.rows)


def error_cell_html(outcome: BlockOutcome, *, detail: ErrorDetail = "summary") -> str:
    """Return the inner HTML of the Data cell for a failed block.

    Every interpolated value is escaped, because this markup is emitted into the document inside the
    trusted region. A message carrying an angle bracket is a message that must not become markup.

    ``detail`` controls how much of the message is shown: ``summary`` (the default) keeps the first
    line, ``detailed`` the whole recorded message. Neither ever shows a traceback -- tracebacks go to
    the log, since a report gets forwarded to people who should not be reading stack frames.
    """
    if outcome.status is BlockStatus.OK:
        raise ValueError("error_cell_html called for an outcome that succeeded")

    error_type = outcome.error_type or _default_error_type(outcome)
    message = outcome.error_message or "no further detail was recorded"
    if detail == "summary":
        message = _first_line(message)[:_SUMMARY_MAX_CHARS]

    attempts = _attempts_phrase(outcome.attempts)
    meta = f"{attempts} \u00b7 {outcome.duration_seconds:.1f} s"

    return (
        '<div class="ai-error" role="alert">'
        '<span class="ai-error__icon" aria-hidden="true">\u26a0</span>'
        f'<span class="ai-error__type">{escape(error_type)}</span>'
        f'<span class="ai-error__message">{escape(message)}</span>'
        f'<span class="ai-error__meta">{escape(meta)}</span>'
        "</div>"
    )


def _default_error_type(outcome: BlockOutcome) -> str:
    """A readable fallback when collection recorded a status but no exception type."""
    if outcome.status is BlockStatus.TIMED_OUT:
        return "Timed out"
    return "Unknown error"


def _first_line(message: str) -> str:
    return message.splitlines()[0].strip() if message.strip() else "no further detail was recorded"


def _attempts_phrase(attempts: int) -> str:
    return f"{attempts} attempt" if attempts == 1 else f"{attempts} attempts"

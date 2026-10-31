"""Row and cell markup.

Two responsibilities, deliberately separated by who owns them:

* :func:`build_row_views` turns assembled :class:`~auto_html_report.domain.report.ReportRow` objects into
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
from typing import Final, Literal

from auto_html_report._naming import DEFAULT_MAX_LENGTH, slug
from auto_html_report.domain.block import BlockOutcome, BlockStatus
from auto_html_report.domain.report import Report, ReportRow
from auto_html_report.rendering.escaping import escape

ErrorDetail = Literal["summary", "detailed"]

#: The accepted detail levels, checked at runtime. The ``Literal`` type is documentation for a typed
#: caller and no defence at all against a value that came from a string -- an environment variable, say.
_ERROR_DETAILS: Final[tuple[str, ...]] = ("summary", "detailed")

#: How much of an error message the summary view shows. Long enough to be actionable, short enough
#: not to turn one cell into a wall of text.
_SUMMARY_MAX_CHARS = 160

_NO_DETAIL_RECORDED = "no further detail was recorded"


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


def build_row_views(
    rows: Sequence[ReportRow], *, detail: ErrorDetail = "summary"
) -> tuple[RowView, ...]:
    """Flatten rows for the template, preserving order and making ``block_id`` unique.

    **A failed row's cell is built here, not during collection.** Collection records the outcome and
    leaves ``html`` empty; this is where an outcome becomes markup, because the error detail level is a
    presentation setting and ``application/`` may not import ``rendering/``
    (``docs/01-overview.md`` §4 rule 2).

    ``block_id`` is the slug of the row's Item text rather than of its block type. Two instances of one
    block type routinely appear in the same report -- "Maintenance -- T-104" and "Maintenance -- T-112"
    -- so a type-derived id would collide, and uniqueness is the property that matters for the
    ``(run_id, block)`` key a future sign-off store would need (ADR-0018).

    Slugging alone does **not** give that uniqueness: "R&D maintenance" and "R D maintenance" both slug
    to ``r-d-maintenance``, as do truncated long texts sharing a prefix. Collisions are therefore
    resolved here, in display order, with a numeric suffix -- so the same report always yields the same
    ids, and a reader can still tell which row is which.
    """
    used: set[str] = set()
    return tuple(
        RowView(
            item=row.item,
            html=row.html
            if row.outcome.status is BlockStatus.OK
            else error_cell_html(row.outcome, detail=detail),
            is_error=row.outcome.status is not BlockStatus.OK,
            block_id=_unique_block_id(row.item, used),
        )
        for row in rows
    )


def build_report_rows(report: Report, *, detail: ErrorDetail = "summary") -> tuple[RowView, ...]:
    """Convenience for the renderer: the view rows of a whole report."""
    return build_row_views(report.rows, detail=detail)


def _unique_block_id(item: str, used: set[str]) -> str:
    """A slug of ``item`` that is not already in ``used``, which is extended in place."""
    candidate = slug(item, fallback="block")
    if candidate not in used:
        used.add(candidate)
        return candidate

    counter = 2
    while True:
        suffix = f"-{counter}"
        # Shorten the base to make room, so the disambiguated id still respects the length cap.
        base = slug(item, max_length=DEFAULT_MAX_LENGTH - len(suffix), fallback="block")
        candidate = f"{base}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def error_cell_html(outcome: BlockOutcome, *, detail: ErrorDetail = "summary") -> str:
    """Return the inner HTML of the Data cell for a failed block.

    Every interpolated value is escaped, because this markup is emitted into the document inside the
    trusted region. A message carrying an angle bracket is a message that must not become markup.

    ``detail`` controls how much of the message is shown: ``summary`` (the default) keeps the first
    non-blank line, ``detailed`` the whole recorded message. Neither ever shows a traceback --
    tracebacks go to the log, since a report gets forwarded to people who should not be reading stack
    frames. An unrecognised value raises rather than silently meaning "the most verbose option
    available", which is the wrong way for a typo to fail.
    """
    if detail not in _ERROR_DETAILS:
        raise ValueError(f"unknown error detail {detail!r}; expected one of {_ERROR_DETAILS}")
    if outcome.status is BlockStatus.OK:
        raise ValueError("error_cell_html called for an outcome that succeeded")

    error_type = outcome.error_type or _default_error_type(outcome)
    message = outcome.error_message or _NO_DETAIL_RECORDED
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
    """The first line with content in it, so a message that merely starts with a newline still shows.

    Taking ``splitlines[0]`` unconditionally was a defect here: a message beginning with a blank line
    produced an empty message span, which is the "looks like a layout bug" outcome the fallback exists
    to prevent.
    """
    for line in message.splitlines():
        if line.strip():
            return line.strip()
    return _NO_DETAIL_RECORDED


def _attempts_phrase(attempts: int) -> str:
    return f"{attempts} attempt" if attempts == 1 else f"{attempts} attempts"

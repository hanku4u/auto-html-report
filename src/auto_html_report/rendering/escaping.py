"""Escaping-first HTML helpers for block authors.

The framework's own interpolations are escaped by the template's ``autoescape``. A block's output is
inserted with ``|safe`` instead, because a block returns trusted HTML by design (ADR-0012) -- so
anything a block interpolates from outside the process is the block's responsibility. These helpers
exist to make the safe path the shortest one. See ``docs/06-output-html.md`` §4.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from html import escape as _html_escape


def escape(text: str) -> str:
    """Escape text for interpolation into HTML text or a quoted attribute.

    Quotes are escaped as well, so the result is safe inside ``title="..."`` and ``alt="..."`` -- the
    place hand-written f-strings most often forget.
    """
    return _html_escape(text, quote=True)


def html_table(*, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Render a data table, escaping every header and every cell.

    Intended for the *inside* of a block's Data cell: it returns a ``<table>``, not a row and not a
    document.

    Cell values are stringified, except that ``None`` becomes an empty cell -- a report showing the
    literal text "None" for missing data is a report nobody wants to forward. Rows are expected to
    match ``headers`` in length; a mismatch raises :class:`ValueError`, because it is a block bug and
    the alternative is a table that silently shifts its columns.

    With no rows, the header row is still emitted: an empty table with named columns is honest, and a
    block that wants a different empty state should return its own markup before calling this.
    """
    if not headers:
        raise ValueError("a table needs at least one column")

    header_cells = "".join(f'<th scope="col">{escape(str(header))}</th>' for header in headers)

    body_rows: list[str] = []
    for index, row in enumerate(rows):
        cells = list(row)
        if len(cells) != len(headers):
            raise ValueError(
                f"row {index} has {len(cells)} cells but the table has {len(headers)} columns"
            )
        body_rows.append("<tr>" + "".join(f"<td>{_cell(value)}</td>" for value in cells) + "</tr>")

    body = "".join(body_rows)
    return (
        '<table class="ai-data-table">'
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
    )


def _cell(value: object) -> str:
    if value is None:
        return ""
    return escape(str(value))

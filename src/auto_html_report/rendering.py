"""HTML helpers for block authors, and the document template."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from html import escape as _html_escape
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from jinja2 import DictLoader, Environment, StrictUndefined

from auto_html_report._version import get_version

if TYPE_CHECKING:
    from matplotlib.figure import Figure


def escape(text: str) -> str:
    """Escape text for HTML content or a quoted attribute."""
    return _html_escape(text, quote=True)


def html_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """A table for a block's Data cell, with every value escaped. ``None`` becomes an empty cell."""
    if not headers:
        raise ValueError("a table needs at least one column")

    body = []
    for index, row in enumerate(rows):
        if len(row) != len(headers):
            raise ValueError(f"row {index} has {len(row)} cells, expected {len(headers)}")
        cells = "".join(f"<td>{'' if cell is None else escape(str(cell))}</td>" for cell in row)
        body.append(f"<tr>{cells}</tr>")

    head = "".join(f'<th scope="col">{escape(str(header))}</th>' for header in headers)
    return (
        f'<table class="ai-data-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def figure_to_data_uri(figure: Figure, *, dpi: int = 100) -> str:
    """A matplotlib figure as a PNG data URI, for ``<img src="...">``.

    Create the figure with ``matplotlib.figure.Figure()`` rather than ``pyplot``: it needs no display, and
    there is nothing to close afterwards.
    """
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi)
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def error_cell(error: Exception) -> str:
    """The Data cell of a row whose ``render`` raised."""
    return (
        '<div class="ai-error" role="alert">'
        '<span class="ai-error__icon" aria-hidden="true">⚠</span>'
        f'<span class="ai-error__type">{escape(type(error).__name__)}</span>'
        f'<span class="ai-error__message">{escape(str(error))}</span>'
        "</div>"
    )


def render_document(
    *,
    title: str,
    subtitle: str | None,
    metadata: Mapping[str, str],
    rows: Sequence[Any],
    generated_at: datetime,
) -> str:
    """The complete document. Each row needs ``title``, ``html`` and ``error``."""
    return (
        _environment()
        .get_template("report.html.j2")
        .render(
            title=title,
            subtitle=subtitle,
            metadata=metadata,
            rows=rows,
            generated_at=generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            version=get_version(),
        )
    )


@lru_cache(maxsize=1)
def _environment() -> Environment:
    templates = files("auto_html_report").joinpath("templates")
    assets = {
        name: templates.joinpath(name).read_text(encoding="utf-8")
        for name in ("report.html.j2", "report.css")
    }
    return Environment(
        loader=DictLoader(assets),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

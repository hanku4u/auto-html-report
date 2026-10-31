"""``render_report``: the pure function that turns an assembled report into an HTML document.

The template and stylesheet are package data, read through ``importlib.resources`` -- never through
``Path(__file__).parent``, which does not exist inside a zipped wheel or some container layers, and
whose failure appears only after deployment. See ``docs/08-packaging.md`` §5.

Pure by contract (``docs/03-report-api.md`` §2): no I/O beyond reading its own packaged assets, no
clock, no randomness. Everything that varies between runs -- the run id, the timestamps, the version --
arrives on the :class:`~auto_html_report.domain.report.Report` or is environment data resolved once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files

from jinja2 import DictLoader, Environment, StrictUndefined

from auto_html_report._version import get_version
from auto_html_report.domain.report import Report
from auto_html_report.rendering.cells import ErrorDetail, RowView, build_report_rows

#: Human-readable form for the header's "Generated" value.
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Compact form for the document title, mirroring the report filename convention in docs/06 §2, so a
#: printed page and the file it came from carry the same token.
_BASIC_FORMAT = "%Y%m%dT%H%M%SZ"

_TEMPLATE_NAME = "report.html.j2"
_STYLESHEET_NAME = "report.css"


@dataclass(frozen=True, slots=True)
class _ReportView:
    """What the template sees. Flat on purpose: the template should not be reaching into domain."""

    title: str
    subtitle: str | None
    run_id: str
    generated_at_iso: str
    title_timestamp: str
    metadata: tuple[tuple[str, str], ...]
    rows: tuple[RowView, ...]
    tool_version: str


def render_report(report: Report, *, error_detail: ErrorDetail = "summary") -> str:
    """Render ``report`` as a complete, self-contained HTML document.

    ``error_detail`` controls how much of a failed block's message the error cell shows; it is a
    presentation setting, which is why the renderer -- not collection -- builds that cell.

    Raises :class:`ValueError` if ``report.generated_at`` is naive. The dataclass documents it as
    timezone-aware UTC; a naive value means something bypassed the injected clock, and formatting it
    anyway would silently shift every timestamp by the host's UTC offset -- a wrong number in a report
    is worse than a loud failure.
    """
    template = _environment().get_template(_TEMPLATE_NAME)
    return template.render(report=_to_view(report, error_detail=error_detail))


def _to_view(report: Report, *, error_detail: ErrorDetail) -> _ReportView:
    return _ReportView(
        title=report.definition.title,
        subtitle=report.definition.subtitle,
        title_timestamp=_format_utc(report.generated_at, _BASIC_FORMAT),
        generated_at_iso=_format_utc(report.generated_at, _ISO_FORMAT),
        run_id=report.run_id,
        metadata=_metadata_items(report.definition.metadata),
        rows=build_report_rows(report, detail=error_detail),
        tool_version=get_version(),
    )


def _metadata_items(metadata: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Metadata as ordered pairs, so the template can iterate without calling ``.items()``."""
    return tuple((name, value) for name, value in metadata.items())


def _format_utc(moment: datetime, fmt: str) -> str:
    if moment.tzinfo is None:
        raise ValueError(
            "Report.generated_at must be timezone-aware; a naive value would be formatted "
            "against the host's local offset. Build reports with the injected Clock."
        )
    return moment.astimezone(UTC).strftime(fmt)


@lru_cache(maxsize=1)
def _environment() -> Environment:
    """Build the Jinja environment once, from the packaged assets.

    **The stylesheet is loaded as a template so the document can ``include`` it verbatim.** Injecting
    it as a variable instead would send it through autoescaping, and entities are not decoded inside
    ``<style>``: a ``font-family: "Segoe UI"`` would arrive as ``&#34;Segoe UI&#34;`` and stop being a
    font name. Literal template text is never autoescaped, so the include keeps the CSS intact while
    leaving row HTML as the only interpolation that needs the ``|safe`` filter.

    ``StrictUndefined`` turns a typo'd or renamed template variable into a loud failure instead of a
    silently blank cell in a document nobody proof-reads.
    """
    loader = DictLoader({name: _read_asset(name) for name in (_TEMPLATE_NAME, _STYLESHEET_NAME)})
    return Environment(
        loader=loader,
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _read_asset(name: str) -> str:
    """Read a packaged asset. ``files()`` works from a source tree, a wheel, or a zip."""
    return (
        files("auto_html_report.rendering").joinpath("templates", name).read_text(encoding="utf-8")
    )

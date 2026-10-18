"""The public entry point: a list of blocks in, a written report out.

This module sits at the package root rather than in ``application/``, and the layering rule is what put
it there: it composes collection (``application/``), rendering, and an injected writer, while
``application/`` may not import ``rendering/`` (``docs/01-overview.md`` §4 rule 2). Composition belongs at
the root, which is allowed to see every layer. The *use case* of gathering data stays where it belongs, in
``application/collect_blocks.py``.

``generate_report`` is deliberately a thin composition and nothing else. If it ever grows logic of its
own, that logic belongs in one of the pieces it calls.
"""

from __future__ import annotations

from datetime import UTC

from auto_inform._naming import slug
from auto_inform.application.collect_blocks import collect_blocks
from auto_inform.application.context import ReportContext
from auto_inform.application.ports import OutputWriter
from auto_inform.domain.report import Report, ReportDefinition, ReportResult
from auto_inform.rendering.shell import render_report

#: Mirror of the report filename convention in ``docs/06-output-html.md`` §2.
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def report_filename(definition: ReportDefinition, report: Report) -> str:
    """The filename for a written report.

    ``<slug(title)>__<slug(event_id)>__<timestamp>.html``, with the event segment present only when the
    definition names an event. The timestamp makes every run's output addressable rather than
    overwriting, and the slugs keep the name safe for a filesystem and an S3 key alike.

    Raises :class:`ValueError` for a naive ``generated_at``, for the same reason ``render_report`` does:
    a timestamp silently shifted by the host's offset would name the file as if it were generated at a
    different moment.
    """
    if report.generated_at.tzinfo is None:
        raise ValueError(
            "Report.generated_at must be timezone-aware; build reports with the injected Clock"
        )

    timestamp = report.generated_at.astimezone(UTC).strftime(TIMESTAMP_FORMAT)
    segments = [slug(definition.title, fallback="report")]
    if definition.event_id:
        segments.append(slug(definition.event_id, fallback="event"))
    segments.append(timestamp)
    return "__".join(segments) + ".html"


def generate_report(
    definition: ReportDefinition,
    *,
    context: ReportContext,
    writer: OutputWriter | None = None,
) -> ReportResult:
    """Collect every block, assemble the report, render it, and write it if a writer was supplied.

    With ``writer=None`` the HTML is returned but not persisted -- the "give me the document and let me
    decide" case, which is why ``ReportResult.location`` is allowed to be ``None``.

    The error detail level comes from settings here, which is the only place that knows both the
    settings and the renderer. A failed block still produces a document: its cell is an error cell and
    the run continues (``docs/03-report-api.md`` §2).
    """
    rows = collect_blocks(definition.blocks, context=context, report_title=definition.title)
    report = Report(
        definition=definition,
        rows=rows,
        generated_at=context.clock.now(),
        run_id=context.run.run_id,
    )
    html = render_report(report, error_detail=context.settings.error_detail)

    location = None
    if writer is not None:
        location = writer.write(filename=report_filename(definition, report), content=html)

    return ReportResult(report=report, html=html, location=location)

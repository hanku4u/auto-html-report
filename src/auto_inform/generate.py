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

import hashlib
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

    ``<slug(title)>__[<slug(event_id)>__]<digest>__<timestamp>.html``. The timestamp makes every run's
    output addressable rather than overwriting, the slugs keep the name safe for a filesystem and an S3
    key alike, and the digest distinguishes two *different* events whose slugs happen to agree -- see
    :func:`_identity_digest`.

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
    segments.append(_identity_digest(definition))
    segments.append(timestamp)
    return "__".join(segments) + ".html"


def _identity_digest(definition: ReportDefinition) -> str:
    """Eight hex characters of a digest over the *raw* title and event id.

    The slugs are lossy on purpose — lower-cased, non-alphanumerics folded, 60-character cap — so two
    distinct events can produce the same segments: ids ``"A"`` and ``"a"``, or two long ids sharing a
    prefix. Locally that was a nuisance (the second write replaced the first); against a shared bucket it
    is silent data loss, and the store would record both events with one URI. The digest is computed from
    the un-slugged pair, so it differs exactly when the identities differ, while the name stays readable.
    """
    material = f"{definition.title}\x00{definition.event_id or ''}".encode()
    return hashlib.sha256(material).hexdigest()[:8]


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
    # One instant for the whole report: captured before collection, handed to the blocks and reused for
    # the Report. Two separate reads of the clock would disagree by the collection duration -- a block
    # that renders `generated_at` would then contradict the document header.
    generated_at = context.clock.now()
    started = context.clock.monotonic()
    context.logger.info(
        "report.start",
        extra={
            "run_id": context.run.run_id,
            "report_title": definition.title,
            "block_count": len(definition.blocks),
        },
    )

    report: Report | None = None
    html: str | None = None
    location = None
    try:
        rows = collect_blocks(
            definition.blocks,
            context=context,
            report_title=definition.title,
            generated_at=generated_at,
        )
        report = Report(
            definition=definition,
            rows=rows,
            generated_at=generated_at,
            run_id=context.run.run_id,
        )
        html = render_report(report, error_detail=context.settings.error_detail)

        if writer is not None:
            location = writer.write(filename=report_filename(definition, report), content=html)
    except Exception as exc:
        # Every failure after `report.start` gets a terminal line. Before this, only the write path
        # did, so a definition or renderer failure left an orphaned start and a reader could not tell
        # whether the report was still running -- see the stage-9 amendment in docs/11.
        context.logger.exception(
            "report.failed",
            extra={
                "run_id": context.run.run_id,
                "report_title": definition.title,
                "error_type": type(exc).__name__,
            },
        )
        raise
    finally:
        # Only when a document exists. `report.finish` says "the document was produced; here is where it
        # went" -- so a failure before rendering ends with `report.failed` alone rather than a finish that
        # claims a document nobody has. A write failure still gets both, with `output_uri: null`.
        if report is not None and html is not None:
            context.logger.info(
                "report.finish",
                extra={
                    "run_id": context.run.run_id,
                    "report_title": definition.title,
                    "failed_blocks": len(report.failed_rows),
                    "duration_ms": round((context.clock.monotonic() - started) * 1000, 1),
                    "output_uri": location.uri if location is not None else None,
                },
            )

    if (
        report is None or html is None
    ):  # pragma: no cover - unreachable without a re-raised exception
        raise AssertionError("internal error: generate_report completed without a document")
    return ReportResult(report=report, html=html, location=location)

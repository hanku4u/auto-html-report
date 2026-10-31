"""The public entry point: collect, assemble, render, write.

Two tests here are more than wiring checks.

The **error-detail** test proves that `Settings.error_detail` actually reaches the rendered cell. It was
wired through `render_report` in stage 5 with no supplier, so `AUTO_HTML_REPORT_ERROR_DETAIL` was silently dead
until `generate_report` connected the two -- and a test that the parameter exists would not have noticed.

The **failed-block** test proves that a report with a red cell is still written. The error cells are the
deliverable; refusing to write because one block failed is the behaviour the framework exists to avoid.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest

from auto_html_report.application.context import RenderContext, ReportContext
from auto_html_report.blocks.maintenance_history import MaintenanceHistoryBlock
from auto_html_report.blocks.wafer_map import WaferMapBlock
from auto_html_report.domain.block import Block
from auto_html_report.domain.errors import OutputError, PermanentBlockError
from auto_html_report.domain.report import OutputLocation, Report, ReportDefinition
from auto_html_report.generate import generate_report, report_filename
from auto_html_report.infrastructure.settings import Settings
from auto_html_report.infrastructure.writers.local_writer import LocalDirectoryWriter
from tests.support.clocks import DEFAULT_START, FakeClock
from tests.support.contexts import make_report_context
from tests.support.http import FakeHttpClient


class _WorkBlock(Block[str]):
    def __init__(self, *, name: str) -> None:
        super().__init__(name=name)

    def collect(self, context: ReportContext) -> str:
        return self.name

    def render(self, data: str, context: RenderContext) -> str:
        return f"<p>{data}</p>"


class _FailingBlock(Block[str]):
    def __init__(self, *, name: str, message: str) -> None:
        super().__init__(name=name)
        self._message = message

    def collect(self, context: ReportContext) -> str:
        raise PermanentBlockError(self._message)

    def render(self, data: str, context: RenderContext) -> str:
        return ""


def _definition(
    *blocks: Block[Any], title: str = "Test report", event_id: str | None = None
) -> ReportDefinition:
    return ReportDefinition(title=title, event_id=event_id, blocks=list(blocks))


def test_generate_report_should_collect_render_and_write_the_document(tmp_path: Path) -> None:
    # given a definition and a writer
    definition = _definition(_WorkBlock(name="First"), _WorkBlock(name="Second"))

    # when a report is generated
    result = generate_report(
        definition, context=make_report_context(), writer=LocalDirectoryWriter(tmp_path)
    )

    # then every block is a row in order, the document was written, and what was written is exactly what
    # was returned -- the two cannot drift
    assert [row.item for row in result.report.rows] == ["First", "Second"]
    assert result.report.all_succeeded
    assert result.location is not None
    assert result.location.kind == "file"
    written = tmp_path / report_filename(definition, result.report)
    assert written.read_text(encoding="utf-8") == result.html


def test_generate_report_without_a_writer_should_return_the_html_without_writing() -> None:
    # given no writer, which is the "give me the document, I will decide" case
    result = generate_report(_definition(_WorkBlock(name="Row")), context=make_report_context())

    # then the HTML is returned and the absence of a location is not an error
    assert result.html.startswith("<!DOCTYPE html>")
    assert result.location is None


def test_generate_report_should_still_write_a_report_when_a_block_fails(tmp_path: Path) -> None:
    # given a definition where the second block fails
    definition = _definition(_WorkBlock(name="Fine"), _FailingBlock(name="Broken", message="no"))

    # when a report is generated
    result = generate_report(
        definition, context=make_report_context(), writer=LocalDirectoryWriter(tmp_path)
    )

    # then the document was still written, with an error cell in it and the failure recorded. A missing
    # report would be a worse outcome than a red cell
    assert not result.report.all_succeeded
    assert 'class="ai-error"' in result.html
    assert (tmp_path / report_filename(definition, result.report)).exists()
    failed = result.report.failed_rows[0]
    assert failed.outcome.error_type == "PermanentBlockError"


@pytest.mark.parametrize(
    ("detail", "second_line_visible"), [("summary", False), ("detailed", True)]
)
def test_generate_report_should_pass_the_error_detail_setting_to_the_renderer(
    detail: str, second_line_visible: bool
) -> None:
    # given a failure whose message runs to two lines, and a configured detail level
    definition = _definition(_FailingBlock(name="Broken", message="first line\nsecond line"))
    context = make_report_context(settings=Settings(error_detail=detail))  # type: ignore[arg-type]

    # when the report is rendered
    result = generate_report(definition, context=context)

    # then the setting decided how much of the message appears. This is the link that was missing in
    # stage 5: the parameter existed, the setting existed, and nothing joined them
    assert ("second line" in result.html) is second_line_visible
    assert "first line" in result.html


def test_generate_report_should_carry_the_run_id_and_generation_time_in_the_header() -> None:
    # given a context with a known run id and clock
    context = make_report_context()

    # when a report is generated
    result = generate_report(_definition(_WorkBlock(name="Row")), context=context)

    # then the document identifies the run and the moment, so a report can be traced back to its log
    assert context.run.run_id in result.html
    assert DEFAULT_START.strftime("%Y-%m-%dT%H:%M:%SZ") in result.html


def test_generate_report_should_log_the_report_start_and_finish_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # given a report with one healthy block and one failing block
    definition = _definition(_WorkBlock(name="Fine"), _FailingBlock(name="Broken", message="no"))
    caplog.set_level(logging.INFO)

    # when it is generated
    generate_report(definition, context=make_report_context())

    # then the two report-level events from 07 §4 are emitted with the documented fields. They were in the
    # contract table but nothing emitted them: a reader would have gone looking for a log line that did
    # not exist
    messages = [record.message for record in caplog.records]
    assert "report.start" in messages
    assert "report.finish" in messages

    started = next(r for r in caplog.records if r.message == "report.start")
    assert started.block_count == 2  # type: ignore[attr-defined]
    finished = next(r for r in caplog.records if r.message == "report.finish")
    assert finished.failed_blocks == 1  # type: ignore[attr-defined]
    assert getattr(finished, "duration_ms", None) is not None


def test_generate_report_with_the_built_in_blocks_should_produce_a_report(tmp_path: Path) -> None:
    # given the two shipped blocks and canned API responses -- no network, no real service
    http = FakeHttpClient(
        {
            "/maintenance/T-104": {
                "status": "ok",
                "records": [
                    {"performed_at": "2026-10-15", "engineer": "A. Rivera", "summary": "Replaced"}
                ],
            },
            "/wafers/LOT-88213/binmap": {"bins": [[0.0, 1.0], [1.0, 0.0]]},
        }
    )
    definition = _definition(
        MaintenanceHistoryBlock(tool_id="T-104"),
        WaferMapBlock(lot_id="LOT-88213"),
        title="LINE 4 — Daily Health",
        event_id="WAFER_FAILURE:LOT-88213",
    )

    # when a report is generated end to end
    result = generate_report(
        definition,
        context=make_report_context(http=http),
        writer=LocalDirectoryWriter(tmp_path),
    )

    # then the table and the chart are both in one self-contained document, and it is on disk under the
    # filename the definition implies
    assert result.report.all_succeeded
    assert 'class="ai-data-table"' in result.html
    assert "data:image/png;base64," in result.html
    written = tmp_path / report_filename(definition, result.report)
    assert written.exists()
    assert "wafer-failure-lot-88213" in written.name


class _StampBlock(Block[str]):
    """Renders the generation instant it was given, after simulating ten seconds of work."""

    def collect(self, context: ReportContext) -> str:
        cast(FakeClock, context.clock).advance(10.0)
        return "x"

    def render(self, data: str, context: RenderContext) -> str:
        return f"<p>{context.generated_at.isoformat()}</p>"


def test_generate_report_should_stamp_blocks_and_header_with_one_instant() -> None:
    # given a block that consumes ten seconds and then renders the instant it was handed
    context = make_report_context()

    # when a report is generated
    result = generate_report(_definition(_StampBlock(name="Stamp")), context=context)

    # then the header carries that same instant. Two independent reads of the clock would differ by the
    # collection duration, and a block rendering the timestamp would contradict the document header
    assert result.report.generated_at.isoformat() in result.html
    assert 'class="ai-header"' in result.html


def test_generate_report_with_a_failing_writer_should_still_close_the_report_event_pair(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # given a writer that cannot write, on the one path where the report does not land
    class _FailingWriter:
        def write(self, *, filename: str, content: str) -> OutputLocation:
            raise OutputError("disk full")

    caplog.set_level(logging.INFO)

    # when a report is generated
    with pytest.raises(OutputError):
        generate_report(
            _definition(_WorkBlock(name="Row")),
            context=make_report_context(),
            writer=_FailingWriter(),
        )

    # then the report-level pair is complete: a start, an explicit failure, and a finish with no URI.
    # Otherwise the log leaves an orphaned report.start on the path a reader most needs to find
    messages = [record.message for record in caplog.records]
    assert "report.start" in messages
    assert "report.failed" in messages
    assert "report.finish" in messages
    failed = next(r for r in caplog.records if r.message == "report.failed")
    # the contract table promises error_type, and every other failure event carries it. getattr because
    # LogRecord's type says nothing about the fields a caller attaches to it
    assert getattr(failed, "error_type", None) == "OutputError"
    assert failed.exc_info is not None
    finished = next(r for r in caplog.records if r.message == "report.finish")
    assert getattr(finished, "output_uri", "missing") is None


def test_a_renderer_failure_should_end_the_report_pair_with_failure_not_a_finish(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # given a renderer that raises, as a bug in the renderer would
    def explode(report: Report, *, error_detail: object) -> str:
        raise RuntimeError("renderer bug")

    monkeypatch.setattr("auto_html_report.generate.render_report", explode)
    caplog.set_level(logging.INFO)

    # when a report is generated
    with pytest.raises(RuntimeError, match="renderer bug"):
        generate_report(_definition(_WorkBlock(name="Row")), context=make_report_context())

    # then the pair ends with report.failed and there is no fabricated finish: before this, a failure
    # after report.start left an orphaned start and a reader could not tell whether the run was still
    # going
    messages = [record.message for record in caplog.records]
    assert "report.start" in messages
    assert "report.failed" in messages
    assert "report.finish" not in messages

"""ReportDefinition validation, Report aggregation, and the value types around them."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from auto_html_report.application.context import RenderContext, ReportContext
from auto_html_report.domain.block import Block, BlockOutcome, BlockStatus
from auto_html_report.domain.errors import ConfigurationError
from auto_html_report.domain.report import (
    OutputLocation,
    Report,
    ReportDefinition,
    ReportResult,
    ReportRow,
)

GENERATED_AT = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
RUN_ID = "9f2c1ab34de5"


class _StubBlock(Block[int]):
    """Takes its Item text as a constructor argument, like a real parameterised block."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name)

    def collect(self, context: ReportContext) -> int:
        return 0

    def render(self, data: int, context: RenderContext) -> str:
        return "<p>stub</p>"


def _block(name: str) -> _StubBlock:
    return _StubBlock(name)


def _definition(*names: str, title: str = "Test report") -> ReportDefinition:
    return ReportDefinition(title=title, blocks=[_block(name) for name in names])


def _outcome(name: str, status: BlockStatus) -> BlockOutcome:
    return BlockOutcome(name=name, status=status, duration_seconds=0.1, attempts=1)


def _report(*statuses: BlockStatus) -> Report:
    """Build a consistent Report: one block per status, rows derived from those same blocks."""
    blocks = [_block(f"block-{index}") for index, _ in enumerate(statuses)]
    return Report(
        definition=ReportDefinition(title="Test report", blocks=list(blocks)),
        rows=tuple(
            ReportRow(item=block.name, html="<p>stub</p>", outcome=_outcome(block.name, status))
            for block, status in zip(blocks, statuses, strict=True)
        ),
        generated_at=GENERATED_AT,
        run_id=RUN_ID,
    )


# --- ReportDefinition ---------------------------------------------------------


def test_report_definition_with_no_blocks_should_raise_configuration_error() -> None:
    # given no blocks at all
    # when a definition is constructed
    # then it is refused immediately: a report with no rows is a programming error, not an empty
    # artefact somebody wanted
    with pytest.raises(ConfigurationError, match="no blocks"):
        ReportDefinition(title="Empty", blocks=[])


def test_report_definition_with_duplicate_block_names_should_raise_configuration_error() -> None:
    # given two blocks that would both render into the Item column as the same text
    # when a definition is constructed
    # then it is refused, because a reader cannot tell the two rows apart
    with pytest.raises(ConfigurationError, match="duplicate block names"):
        _definition("Maintenance", "Maintenance")


def test_report_definition_with_duplicates_should_name_every_offender() -> None:
    # given several distinct duplicated names
    # when construction fails
    # then all of them are reported, not just the first, so one round trip fixes the whole report
    with pytest.raises(ConfigurationError) as failure:
        _definition("A", "B", "A", "B", "C")

    message = str(failure.value)
    assert "['A', 'B']" in message


def test_report_definition_with_repeated_names_should_report_each_offender_once() -> None:
    # given a name appearing three times
    # when construction fails
    # then it is listed once, because a duplicate report that repeats itself is just noise
    with pytest.raises(ConfigurationError) as failure:
        _definition("A", "A", "A")

    assert str(failure.value).count("'A'") == 1


def test_report_definition_with_distinct_names_should_construct() -> None:
    # given two blocks with different names
    # when a definition is constructed
    # then it holds them in the order supplied, which is the display order of the rows
    definition = _definition("First", "Second")

    assert [block.name for block in definition.blocks] == ["First", "Second"]


def test_report_definition_duplicate_check_should_use_the_effective_name_not_the_class() -> None:
    # given two instances of the same block class, distinguished only by name=
    # when a definition is constructed
    # then it is accepted: duplicate detection keys on the Item text, not on the block type, which is
    # what makes one block type usable for several rows
    definition = ReportDefinition(
        title="Two tools",
        blocks=[_StubBlock("Maintenance -- T-104"), _StubBlock("Maintenance -- T-112")],
    )

    assert len(definition.blocks) == 2


def test_report_definition_metadata_should_not_be_shared_between_instances() -> None:
    # given two definitions constructed without metadata
    # when one accumulates a metadata entry
    # then the other is unaffected: a mutable default shared across every instance would be a
    # genuinely nasty bug to diagnose
    first = _definition("A")
    second = _definition("B")

    assert dict(first.metadata) == {}
    assert dict(second.metadata) == {}
    assert first.metadata is not second.metadata


def test_report_definition_should_be_frozen() -> None:
    # given a built definition
    definition = _definition("A")

    # when a field is reassigned
    # then it is refused, so a definition cannot be mutated between construction and assembly
    with pytest.raises(FrozenInstanceError):
        definition.title = "Renamed"  # type: ignore[misc]


def test_report_definition_subtitle_should_default_to_none() -> None:
    assert _definition("A").subtitle is None


# --- Report aggregation -------------------------------------------------------


def test_report_all_succeeded_with_every_row_ok_should_be_true() -> None:
    # given a report whose every block succeeded
    report = _report(BlockStatus.OK, BlockStatus.OK)

    # when success is asked for
    # then it holds, which is what lets the job exit zero
    assert report.all_succeeded is True
    assert report.failed_rows == ()


def test_report_all_succeeded_with_one_failed_row_should_be_false() -> None:
    # given a report with one failed block among healthy ones
    report = _report(BlockStatus.OK, BlockStatus.FAILED, BlockStatus.OK)

    # when success is asked for
    # then it does not hold: the report is still produced and written, but the run exits non-zero so
    # somebody looks at the red row
    assert report.all_succeeded is False


def test_report_all_succeeded_with_one_timed_out_row_should_be_false() -> None:
    # given a block that overran its budget
    report = _report(BlockStatus.TIMED_OUT)

    # when success is asked for
    # then a timeout counts against the run exactly as a failure does
    assert report.all_succeeded is False


def test_report_failed_rows_should_list_non_ok_rows_in_display_order() -> None:
    # given a report with failures separated by healthy rows
    report = _report(
        BlockStatus.OK,
        BlockStatus.FAILED,
        BlockStatus.OK,
        BlockStatus.TIMED_OUT,
        BlockStatus.OK,
    )

    # when the failures are collected
    # then they appear in display order, not completion order, and only the failures are included
    assert [row.outcome.status for row in report.failed_rows] == [
        BlockStatus.FAILED,
        BlockStatus.TIMED_OUT,
    ]
    assert [row.item for row in report.failed_rows] == ["block-1", "block-3"]


def test_report_rows_should_preserve_the_order_given() -> None:
    # given a report assembled from blocks in a known order
    report = _report(BlockStatus.OK, BlockStatus.OK, BlockStatus.OK)

    # when the rows are read
    # then the order is the definition order, which is the whole reason collection returns an
    # ordered tuple rather than yielding as blocks finish
    assert [row.item for row in report.rows] == ["block-0", "block-1", "block-2"]


def test_report_should_be_frozen() -> None:
    # given an assembled report
    report = _report(BlockStatus.OK)

    # when a field is reassigned
    # then it is refused: the report is the record of what was built
    with pytest.raises(FrozenInstanceError):
        report.run_id = "something else"  # type: ignore[misc]


# --- Value types around the report -------------------------------------------


def test_report_row_should_expose_item_html_and_outcome() -> None:
    # given a row produced by collection
    row = ReportRow(
        item="Maintenance", html="<p>x</p>", outcome=_outcome("Maintenance", BlockStatus.OK)
    )

    # when it is inspected
    # then item is the Item column, html is the inner HTML of the Data cell only, and the outcome
    # carries the audit information
    assert row.item == "Maintenance"
    assert row.html == "<p>x</p>"
    assert row.outcome.name == "Maintenance"


def test_report_row_should_have_no_check_field() -> None:
    # given the Check column is structural and emitted empty by the renderer
    # when a row is inspected
    # then there is no check field to populate: a field that can only ever hold "" is bloat
    row = ReportRow(item="A", html="", outcome=_outcome("A", BlockStatus.OK))

    assert not hasattr(row, "check")


def test_output_location_should_be_frozen_and_describe_itself() -> None:
    # given a written report's destination
    location = OutputLocation(kind="file", uri="file:///srv/reports/report.html")

    # when it is inspected
    # then the kind distinguishes a local file from an S3 object, and the URI is unambiguous
    assert (location.kind, location.uri) == ("file", "file:///srv/reports/report.html")
    with pytest.raises(FrozenInstanceError):
        location.uri = "elsewhere"  # type: ignore[misc]


def test_report_result_without_a_writer_should_carry_no_location() -> None:
    # given a caller that asked for HTML and supplied no writer
    result = ReportResult(report=_report(BlockStatus.OK), html="<html></html>", location=None)

    # when the result is inspected
    # then the HTML is present and the absence of a location is not an error, it is the
    # "let me decide what to do with this" case
    assert result.html == "<html></html>"
    assert result.location is None


# --- the optional event identity, and its positional shape (review item 1) ----


def test_report_definition_should_keep_metadata_as_its_fourth_positional_argument() -> None:
    # given a caller that builds a definition positionally, which the type has always allowed
    definition = ReportDefinition("T", [_StubBlock("Row")], None, {"Lot": "LOT-1"})

    # when the fields are read
    # then the mapping is metadata and no event was invented from it: event_id is keyword-only, so adding
    # it could not re-point an argument that already existed
    assert dict(definition.metadata) == {"Lot": "LOT-1"}
    assert definition.event_id is None


def test_report_definition_event_id_should_be_keyword_only() -> None:
    # given the public parameter list
    parameter = inspect.signature(ReportDefinition).parameters["event_id"]

    # then the field cannot be passed positionally, which is the property that keeps the fourth
    # positional argument meaning what it always meant
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_report_definition_with_a_non_string_event_id_should_raise_type_error() -> None:
    # given a mapping where an event identity belongs -- the mistake a positional caller used to make
    # silently, only to fail later inside report_filename
    # when the definition is constructed
    # then it is refused at the point of the mistake
    with pytest.raises(TypeError, match="event_id must be a string"):
        ReportDefinition("T", [_StubBlock("Row")], event_id={"Lot": "LOT-1"})  # type: ignore[arg-type]

"""Row view models and the error cell.

The error cell matters more than its size suggests: it is the only framework-authored HTML that ends up
inside the trusted region of the document, so it has no second line of defence behind it.
"""

from __future__ import annotations

import pytest

from auto_inform._naming import DEFAULT_MAX_LENGTH
from auto_inform.domain.block import BlockStatus
from auto_inform.rendering.cells import build_row_views, error_cell_html
from tests.support.builders import make_outcome, make_report, make_row

# --- row views ----------------------------------------------------------------


def test_build_row_views_should_preserve_row_order() -> None:
    # given rows in a known order
    report = make_report(make_row("First"), make_row("Second"), make_row("Third"))

    # when the views are built
    # then the order is untouched, because display order is the definition order
    assert [view.item for view in build_row_views(report.rows)] == ["First", "Second", "Third"]


@pytest.mark.parametrize("status", [BlockStatus.FAILED, BlockStatus.TIMED_OUT])
def test_build_row_views_should_mark_non_ok_rows_as_errors(status: BlockStatus) -> None:
    # given a row whose block did not succeed
    report = make_report(make_row("Flaky", status=status))

    # when the view is built
    # then it is flagged, which is what makes the renderer put the error class on that cell
    assert build_row_views(report.rows)[0].is_error is True


def test_build_row_views_should_not_mark_a_successful_row_as_an_error() -> None:
    # given a healthy row
    report = make_report(make_row("Fine"))

    # when the view is built
    # then it is not flagged
    assert build_row_views(report.rows)[0].is_error is False


def test_build_row_views_should_derive_the_block_id_from_the_item_text() -> None:
    # given an Item text with punctuation and a parameter in it
    report = make_report(make_row("Maintenance history — T-104"))

    # when the view is built
    # then the data-block attribute is a slug that still identifies the row
    assert build_row_views(report.rows)[0].block_id == "maintenance-history-t-104"


def test_build_row_views_should_give_two_instances_of_one_block_type_distinct_ids() -> None:
    # given two rows from the same block type, differing only by parameter
    report = make_report(make_row("Maintenance — T-104"), make_row("Maintenance — T-112"))

    # when the views are built
    # then the ids differ -- a type-derived id would collide here, and uniqueness is exactly the
    # property a future (run_id, block) key would depend on
    first, second = build_row_views(report.rows)
    assert first.block_id != second.block_id


def test_build_row_views_should_fall_back_when_the_item_text_slugs_to_nothing() -> None:
    # given an Item text with nothing sluggable in it
    report = make_report(make_row("🎉"))

    # when the view is built
    # then the attribute is still usable rather than empty
    assert build_row_views(report.rows)[0].block_id == "block"


def test_build_row_views_should_pass_block_html_through_untouched() -> None:
    # given a row carrying markup a block produced
    report = make_report(make_row("Row", "<strong>trusted</strong>"))

    # when the view is built
    # then the markup is untouched here: escaping it would double-escape it, since this value goes
    # into the document through the template's one |safe filter
    assert build_row_views(report.rows)[0].html == "<strong>trusted</strong>"


# --- the error cell -----------------------------------------------------------


def test_error_cell_html_should_escape_a_message_that_looks_like_markup() -> None:
    # given an exception whose message carries markup -- which a hostile API response can arrange
    outcome = make_outcome(
        "Flaky",
        BlockStatus.FAILED,
        attempts=1,
        error_type="HTTPError",
        error_message='<script>alert("xss")</script>',
    )

    # when the error cell is built
    # then the markup is escaped, because this string is emitted inside the trusted region with no
    # escaping behind it
    cell = error_cell_html(outcome)

    assert "<script>" not in cell
    assert "&lt;script&gt;" in cell


def test_error_cell_html_should_escape_the_error_type() -> None:
    # given an exception type that somehow contains markup (a dynamically created class, say)
    outcome = make_outcome("Flaky", BlockStatus.FAILED, error_type="<b>Bold</b>")

    # when the error cell is built
    # then the type is escaped too: it is just as much external text as the message
    assert "&lt;b&gt;" in error_cell_html(outcome)


def test_error_cell_html_should_carry_the_icon_type_message_and_meta() -> None:
    # given a failed block with a full record
    outcome = make_outcome(
        "Maintenance",
        BlockStatus.FAILED,
        duration_seconds=61.2,
        attempts=3,
        error_type="TimeoutError",
        error_message="maintenance service did not respond",
    )

    # when the error cell is built
    # then every documented part is present, including the glyph that makes the cell identifiable
    # without relying on colour
    cell = error_cell_html(outcome)

    assert 'role="alert"' in cell
    assert "\u26a0" in cell
    assert "TimeoutError" in cell
    assert "maintenance service did not respond" in cell
    assert "3 attempts \u00b7 61.2 s" in cell


def test_error_cell_html_with_summary_detail_should_show_only_the_first_line() -> None:
    # given a multi-line message, as a chained traceback summary often is
    outcome = make_outcome(
        "Flaky", BlockStatus.FAILED, error_message="first line\nsecond line\nthird line"
    )

    # when the summary view is built
    # then only the first line appears, so one bad block cannot turn its cell into a wall of text
    cell = error_cell_html(outcome, detail="summary")

    assert "first line" in cell
    assert "second line" not in cell


def test_error_cell_html_with_detailed_should_show_the_whole_recorded_message() -> None:
    # given the same multi-line message
    outcome = make_outcome(
        "Flaky", BlockStatus.FAILED, error_message="first line\nsecond line\nthird line"
    )

    # when the detailed view is built
    # then the whole message is shown
    cell = error_cell_html(outcome, detail="detailed")

    assert "second line" in cell
    assert "third line" in cell


def test_error_cell_html_should_singularise_a_single_attempt() -> None:
    # given a block that failed on its first and only try
    outcome = make_outcome("Flaky", BlockStatus.FAILED, attempts=1)

    # when the error cell is built
    # then it reads "1 attempt" and not "1 attempts"
    assert "1 attempt \u00b7" in error_cell_html(outcome)


def test_error_cell_html_without_a_recorded_type_should_name_a_timeout_as_one() -> None:
    # given a timed-out block for which collection recorded no exception type
    outcome = make_outcome("Slow", BlockStatus.TIMED_OUT, error_type=None)

    # when the error cell is built
    # then it says something truthful about what happened rather than "UnknownError"
    assert "Timed out" in error_cell_html(outcome)


def test_error_cell_html_without_a_recorded_message_should_say_so() -> None:
    # given a failure with no message recorded
    outcome = make_outcome("Flaky", BlockStatus.FAILED, error_message=None)

    # when the error cell is built
    # then it states that no detail was recorded, instead of rendering an empty element that looks
    # like a layout bug
    assert "no further detail was recorded" in error_cell_html(outcome)


def test_error_cell_html_for_a_successful_outcome_should_raise_value_error() -> None:
    # given an outcome that succeeded
    outcome = make_outcome("Fine", BlockStatus.OK)

    # when an error cell is requested for it
    # then it is refused: a success has nothing to show, and silently rendering one would hide a
    # caller confusing status with success
    with pytest.raises(ValueError, match="succeeded"):
        error_cell_html(outcome)


def test_error_cell_html_with_an_unknown_detail_level_should_raise_value_error() -> None:
    # given a detail level that is not one of the two documented values -- a typo, or a value that
    # arrived as a string rather than through the Literal type
    outcome = make_outcome("Flaky", BlockStatus.FAILED, error_message="first\nsecond")

    # when the error cell is built
    # then it is refused, rather than silently meaning "the most verbose option available" and
    # widening what the report exposes
    with pytest.raises(ValueError, match="unknown error detail"):
        error_cell_html(outcome, detail="Detailed")  # type: ignore[arg-type]


def test_error_cell_html_with_a_message_starting_with_a_blank_line_should_show_the_next_line() -> (
    None
):
    # given a message whose first line is empty, which a chained summary routinely produces
    outcome = make_outcome("Flaky", BlockStatus.FAILED, error_message="\nsecond line has content")

    # when the summary view is built
    cell = error_cell_html(outcome, detail="summary")

    # then it shows the second line rather than an empty span, which is precisely the "looks like a
    # layout bug" outcome this fallback exists to prevent
    assert "second line has content" in cell
    assert '<span class="ai-error__message"></span>' not in cell


def test_error_cell_html_with_an_entirely_blank_message_should_say_so() -> None:
    # given a message with nothing in it but whitespace
    outcome = make_outcome("Flaky", BlockStatus.FAILED, error_message="\n\n   \n")

    # when the summary view is built
    # then it falls back rather than rendering an empty span
    assert "no further detail was recorded" in error_cell_html(outcome, detail="summary")


# --- block id uniqueness -------------------------------------------------------


def test_build_row_views_should_disambiguate_item_texts_that_slug_identically() -> None:
    # given two Item texts that differ but slug to the same token -- a real collision, not a contrived
    # one: the ampersand and the space both become a hyphen
    report = make_report(make_row("R&D maintenance"), make_row("R D maintenance"))

    # when the views are built
    # then the ids differ. Slugging alone does not give uniqueness, and uniqueness is the property the
    # future (run_id, block) sign-off key depends on (ADR-0018)
    first, second = build_row_views(report.rows)

    assert first.block_id == "r-d-maintenance"
    assert second.block_id == "r-d-maintenance-2"


@pytest.mark.parametrize(
    ("first_item", "second_item"),
    [
        ("R&D maintenance", "R D maintenance"),
        ("A/B check", "A-B check"),
        ("Line 4 — T-104", "Line 4 / T-104"),
    ],
)
def test_build_row_views_should_keep_ids_unique_across_every_known_collision(
    first_item: str, second_item: str
) -> None:
    # given each pair of distinct Item texts known to slug identically
    report = make_report(make_row(first_item), make_row(second_item))

    # when the views are built
    # then no two rows share an id
    ids = [view.block_id for view in build_row_views(report.rows)]
    assert len(set(ids)) == 2, ids


def test_build_row_views_should_keep_a_disambiguated_id_within_the_length_cap() -> None:
    # given two long Item texts sharing a prefix, so their capped slugs also collide
    long_item = "Maintenance window for tool T-104 during the October shutdown period"
    report = make_report(make_row(long_item), make_row(f"{long_item} (repeat)"))

    # when the views are built
    # then both ids respect the documented cap and still differ, which is what makes the suffix safe
    # to use in a filename or an S3 key as well as an attribute
    ids = [view.block_id for view in build_row_views(report.rows)]

    assert len(set(ids)) == 2
    assert all(len(value) <= DEFAULT_MAX_LENGTH for value in ids)


def test_build_row_views_should_disambiguate_in_display_order_deterministically() -> None:
    # given the same colliding pair, built twice
    items = ["R&D maintenance", "R D maintenance"]
    first_pass = [
        view.block_id for view in build_row_views(make_report(*map(make_row, items)).rows)
    ]
    # and the same pair in the opposite order
    second_pass = [
        view.block_id for view in build_row_views(make_report(*map(make_row, reversed(items))).rows)
    ]

    # when both are built
    # then the suffix follows display order, so a given report always yields the same ids -- a report
    # that renumbered its rows between runs would break any store keyed on them
    assert first_pass == ["r-d-maintenance", "r-d-maintenance-2"]
    assert second_pass == ["r-d-maintenance", "r-d-maintenance-2"]

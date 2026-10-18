"""``render_report``: the document contract from ``docs/06-output-html.md`` §3, §4 and §6.

These are structural assertions. The byte-level snapshot lives in ``test_rendering_golden.py``; the
"does it fetch anything" question lives in ``test_rendering_containment.py``. Here it is: is the right
thing in the right place, and is everything the framework interpolates escaped?
"""

from __future__ import annotations

import re
from datetime import datetime
from importlib.resources import files

import pytest

from auto_inform.domain.block import BlockStatus
from auto_inform.rendering.escaping import html_table
from auto_inform.rendering.shell import render_report
from tests.support.builders import make_report, make_row
from tests.support.html_audit import strip_css_comments


def test_render_report_should_produce_a_complete_standalone_document() -> None:
    # given a report with one row
    document = render_report(make_report(make_row("Row"), title="Daily Health"))

    # when it is rendered
    # then the document declares itself, declares its encoding, and wraps the content in a page
    assert document.startswith("<!DOCTYPE html>")
    assert '<html lang="en">' in document
    assert '<meta charset="utf-8">' in document
    assert '<meta name="viewport"' in document
    assert document.rstrip().endswith("</html>")


def test_render_report_should_escape_the_title_and_metadata_values() -> None:
    # given a title and metadata carrying markup -- a lot name from a vendor API can contain anything
    document = render_report(
        make_report(
            make_row("Row"),
            title="<script>alert(1)</script>",
            metadata={"Lot": '<img src=x onerror="alert(1)">'},
        )
    )

    # when it is rendered
    # then nothing became markup: the framework's own interpolations go through autoescaping
    assert "<script>" not in document
    assert "&lt;script&gt;" in document
    assert "<img src=x" not in document
    assert "&lt;img" in document


def test_render_report_should_escape_the_item_text_of_each_row() -> None:
    # given a block name carrying markup
    document = render_report(make_report(make_row("<b>Maintenance</b>")))

    # when it is rendered
    # then the Item cell shows the text rather than interpreting it
    assert "&lt;b&gt;Maintenance&lt;/b&gt;" in document


def test_render_report_should_not_escape_block_supplied_html() -> None:
    # given a row whose block produced markup
    document = render_report(make_report(make_row("Row", "<strong>from the block</strong>")))

    # when it is rendered
    # then the markup survives: this is the trust boundary, and escaping here would double-escape
    # every block that correctly escaped its own data (ADR-0012)
    assert "<strong>from the block</strong>" in document


def test_render_report_should_render_one_row_per_report_row_in_definition_order() -> None:
    # given three rows
    document = render_report(make_report(make_row("First"), make_row("Second"), make_row("Third")))

    # when it is rendered
    # then there is one row each, in order
    assert document.count('<tr class="ai-row"') == 3
    assert re.search(r"First.*Second.*Third", document, flags=re.DOTALL)


def test_render_report_should_mark_error_rows_and_leave_healthy_ones_unmarked() -> None:
    # given one healthy row, one failed row and one timed-out row
    failed = make_row("Failed", html="", status=BlockStatus.FAILED, error_type="HTTPError")
    timed_out = make_row("Slow", html="", status=BlockStatus.TIMED_OUT, error_type="TimeoutError")
    document = render_report(make_report(make_row("Fine"), failed, timed_out))

    # when it is rendered
    # then exactly the two bad rows carry the error class, and their cells carry the error markup.
    # Asserted on the full td markup rather than the bare class name: the name also appears in the
    # inlined stylesheet, so counting it would pass on the CSS rule alone
    assert document.count('<td class="ai-cell ai-cell--data ai-cell--error">') == 2
    assert document.count('<td class="ai-cell ai-cell--data">') == 1
    assert document.count('class="ai-error"') == 2


def test_render_report_should_emit_an_empty_check_cell_for_every_row() -> None:
    # given a report of three rows
    document = render_report(make_report(make_row("A"), make_row("B"), make_row("C")))

    # when it is rendered
    # then every row has the Check cell present but empty -- the column is structural, reserved for
    # human sign-off in a later version (ADR-0018)
    assert document.count('<td class="ai-cell ai-cell--check"></td>') == 3
    assert '<th scope="col">Check</th>' in document


def test_render_report_should_carry_the_run_id_and_both_timestamp_forms() -> None:
    # given a fixed generation time
    document = render_report(make_report(make_row("Row")))

    # when it is rendered
    # then the header shows the run id and a human-readable timestamp, and the title carries the
    # compact form that matches the report filename convention
    assert "<dt>Run</dt><dd>9f2c1ab34de5</dd>" in document
    assert "<dt>Generated</dt><dd>2026-10-17T09:00:00Z</dd>" in document
    assert "<title>Test report — 20261017T090000Z</title>" in document


def test_render_report_should_append_metadata_in_insertion_order_after_run_and_generated() -> None:
    # given metadata in a deliberate order
    document = render_report(
        make_report(make_row("Row"), metadata={"Lot": "LOT-88213", "Tool": "T-104"})
    )

    # when it is rendered
    # then Run and Generated come first and the caller's entries follow in the order given, so a
    # reader sees the same header shape in every report
    positions = [document.index(text) for text in ("Run", "Generated", "Lot", "Tool")]
    assert positions == sorted(positions)


def test_render_report_without_a_subtitle_should_omit_the_element_entirely() -> None:
    # given a report with no subtitle
    document = render_report(make_report(make_row("Row")))

    # when it is rendered
    # then the element is absent rather than empty, because an empty paragraph still takes up space
    # (assert on the element, not the string: the class name also appears in the inlined stylesheet)
    assert '<p class="ai-header__subtitle">' not in document


def test_render_report_with_a_subtitle_should_include_it() -> None:
    # given a report with a subtitle
    document = render_report(make_report(make_row("Row"), subtitle="Failed wafer — EVT-88213"))

    # when it is rendered
    # then it appears in the header
    assert '<p class="ai-header__subtitle">Failed wafer — EVT-88213</p>' in document


def test_render_report_should_name_the_tool_and_its_version_in_the_footer() -> None:
    # given any report
    document = render_report(make_report(make_row("Row")))

    # when it is rendered
    # then the footer identifies the generator, so a reader can tell which build produced the file
    # -- the version itself is resolved at runtime and normalised away in the golden comparison
    assert re.search(r"<footer class=\"ai-footer\">auto_inform \S+</footer>", document)


def test_render_report_with_a_naive_generated_at_should_raise_value_error() -> None:
    # given a report whose timestamp lost its timezone, which only happens if something bypassed the
    # injected clock
    report = make_report(make_row("Row"), generated_at=datetime(2026, 10, 17, 9, 0))

    # when it is rendered
    # then it fails loudly: formatting it anyway would shift every timestamp by the host's UTC
    # offset, and a wrong timestamp in a report is worse than an error
    with pytest.raises(ValueError, match="timezone-aware"):
        render_report(report)


def test_render_report_should_be_deterministic_for_the_same_report() -> None:
    # given one report object
    report = make_report(make_row("Row", html_table(headers=["A"], rows=[["1"]])))

    # when it is rendered twice
    # then the documents are identical, which is what "pure" has to mean for golden files to work
    assert render_report(report) == render_report(report)


def test_render_report_should_convert_an_offset_timestamp_to_utc() -> None:
    # given a timestamp expressed in another zone
    from datetime import timedelta, timezone

    report = make_report(
        make_row("Row"),
        generated_at=datetime(2026, 10, 17, 11, 0, tzinfo=timezone(timedelta(hours=2))),
    )

    # when it is rendered
    # then it is normalised to UTC, so two reports from different zones are comparable
    assert "<dd>2026-10-17T09:00:00Z</dd>" in render_report(report)


# --- the trust boundary, and the packaged assets ------------------------------


def test_the_report_template_should_contain_exactly_one_unescaped_interpolation() -> None:
    # given the page template
    template = files("auto_inform.rendering").joinpath("templates", "report.html.j2").read_text()

    # when every `|safe` filter in it is counted
    # then there is exactly one, and it is the row HTML: the whole trust boundary of ADR-0012 rests
    # on that number staying at one, so a second one added casually should fail this test
    assert template.count("|safe") == 1


def test_packaged_rendering_assets_should_be_readable_through_importlib_resources() -> None:
    # given the assets are package data
    base = files("auto_inform.rendering").joinpath("templates")

    # when they are read the way the renderer reads them
    template = base.joinpath("report.html.j2").read_text(encoding="utf-8")
    stylesheet = base.joinpath("report.css").read_text(encoding="utf-8")

    # then both are present and non-trivial: a missing file here is the failure that otherwise only
    # appears after deployment
    assert len(template) > 500
    assert len(stylesheet) > 500


def test_the_packaged_stylesheet_should_not_reference_anything_external() -> None:
    # given the stylesheet that gets inlined into every document
    stylesheet = files("auto_inform.rendering").joinpath("templates", "report.css").read_text()

    # when it is inspected for the two constructs that make a browser fetch something, ignoring the
    # comment that documents the rule
    effective = strip_css_comments(stylesheet)

    # then neither appears, which is what keeps a report one self-contained file
    assert "@import" not in effective
    assert "url(" not in effective

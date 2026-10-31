"""The escaping helpers a block author is meant to reach for."""

from __future__ import annotations

import pytest

from auto_html_report.rendering.escaping import escape, html_table


def test_escape_should_escape_markup_and_both_quote_styles() -> None:
    # given text containing markup and quotes
    # when it is escaped
    # then the angle brackets are neutralised and quotes are escaped too, so the result is safe
    # inside an attribute as well as in text -- the case hand-written f-strings forget
    escaped = escape("<a href=\"x\" title='y'>bold</a>")

    assert "<" not in escaped
    assert ">" not in escaped
    assert '"' not in escaped
    assert "'" not in escaped


def test_html_table_should_escape_every_header_and_cell() -> None:
    # given a table whose contents include markup
    # when it is rendered
    # then nothing in it can become a tag, which is the whole point of the helper
    table = html_table(headers=["<script>"], rows=[["<img src=x onerror=alert(1)>"]])

    assert "<script>" not in table
    assert "<img" not in table
    assert "&lt;script&gt;" in table


def test_html_table_should_render_none_as_an_empty_cell() -> None:
    # given a missing value
    # when the row is rendered
    # then the cell is empty rather than showing the word "None", which is the sort of thing that
    # makes a report look broken
    table = html_table(headers=["A"], rows=[[None]])

    assert "<td></td>" in table
    assert "None" not in table


def test_html_table_should_stringify_non_string_cells() -> None:
    # given numbers, which is what a block usually has
    # when the row is rendered
    # then they are stringified rather than raising, because coercing here saves every block doing it
    table = html_table(headers=["Yield", "Count"], rows=[[0.8125, 42]])

    assert "<td>0.8125</td>" in table
    assert "<td>42</td>" in table


def test_html_table_with_a_row_of_the_wrong_width_should_raise_value_error() -> None:
    # given a row that does not match the header count
    # when it is rendered
    # then it is refused with the row index, because the alternative is a table that silently shifts
    # every column after the mistake
    with pytest.raises(ValueError, match="row 0 has 1 cells"):
        html_table(headers=["A", "B"], rows=[["only-one"]])


def test_html_table_without_headers_should_raise_value_error() -> None:
    # given no columns at all
    # when a table is rendered
    # then it is refused: a table with no columns is not a table
    with pytest.raises(ValueError, match="at least one column"):
        html_table(headers=[], rows=[])


def test_html_table_with_no_rows_should_still_render_its_header() -> None:
    # given no data
    # when the table is rendered
    # then the header survives, because an empty table with named columns is honest -- a block
    # wanting a friendlier empty state returns its own markup instead
    table = html_table(headers=["Date", "Engineer"], rows=[])

    assert '<th scope="col">Date</th>' in table
    assert "<tbody></tbody>" in table


def test_html_table_should_return_a_table_and_not_a_row_or_a_document() -> None:
    # given a rendered table
    table = html_table(headers=["A"], rows=[["1"]])

    # when its edges are inspected
    # then it is exactly a table: the Data cell wrapper and the page are the framework's business
    assert table.startswith('<table class="ai-data-table">')
    assert table.endswith("</table>")
    assert "<td" not in table.split("<tbody>")[0]


def test_html_table_should_take_its_arguments_as_keywords() -> None:
    # given the documented call shape
    # when the arguments are passed positionally instead
    # then it is a TypeError, so a mis-ordered call cannot silently transpose headers and rows
    with pytest.raises(TypeError):
        html_table(["A"], [["1"]])  # type: ignore[call-arg]

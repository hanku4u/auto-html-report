"""The block-author helpers, and the guarantee that a report loads nothing from the network."""

from __future__ import annotations

import subprocess
import sys

import pytest

from auto_html_report import Block, build_report, escape, figure_to_data_uri, html_table
from tests.html_audit import audit


def test_escape_should_cover_quotes_for_use_inside_attributes() -> None:
    # given text that could break out of an attribute
    # when it is escaped
    # then every significant character is neutralised
    assert escape("""<a b="c" d='e'>&""") == "&lt;a b=&quot;c&quot; d=&#x27;e&#x27;&gt;&amp;"


def test_html_table_should_escape_every_value_and_blank_out_none() -> None:
    # given headers and cells holding markup and a None
    # when a table is built
    table = html_table(["<H>"], [("<c>",), (None,)])

    # then nothing became markup, and None isn't shown as text
    assert "&lt;H&gt;" in table
    assert "&lt;c&gt;" in table
    assert "None" not in table
    assert "<td></td>" in table


@pytest.mark.parametrize(
    ("headers", "rows", "message"),
    [([], [], "at least one column"), (["A"], [("x", "y")], "row 0 has 2 cells")],
)
def test_html_table_should_refuse_a_malformed_table(
    headers: list[str], rows: list[tuple[str, ...]], message: str
) -> None:
    # given a table whose shape is wrong
    # when it is built
    # then it is refused rather than silently shifting columns
    with pytest.raises(ValueError, match=message):
        html_table(headers, rows)


def test_a_figure_should_become_an_embedded_png_without_pyplot() -> None:
    # given a figure built without pyplot's global state
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    figure = Figure(figsize=(1, 1))
    figure.add_subplot().plot([0, 1], [0, 1])

    # when it is converted
    uri = figure_to_data_uri(figure)

    # then it's a PNG data URI
    assert uri.startswith("data:image/png;base64,iVBOR")


def test_importing_the_package_should_not_import_matplotlib() -> None:
    # given a fresh interpreter
    # when the package is imported
    completed = subprocess.run(
        [sys.executable, "-c", "import sys, auto_html_report; print('matplotlib' in sys.modules)"],
        check=True,
        capture_output=True,
        text=True,
    )

    # then matplotlib stays optional
    assert completed.stdout.strip() == "False"


def _document() -> str:
    class Table(Block):
        title = "Table"

        def render(self, data: None) -> str:
            return html_table(["A"], [("x",)])

    class Chart(Block):
        title = "Chart"

        def render(self, data: None) -> str:
            return '<img src="data:image/png;base64,AAAA" alt="">'

    class Link(Block):
        title = "Link"

        def render(self, data: None) -> str:
            return '<a href="https://tickets.example/1">ticket</a>'

    class Broken(Block):
        title = "Broken"

        def render(self, data: None) -> str:
            raise ValueError("no")

    return build_report("T", [Table(), Chart(), Link(), Broken()], subtitle="S").html


def test_a_report_should_load_nothing_from_the_network() -> None:
    # given a report using tables, a chart, a link and an error row
    # when every reason to make a request is collected
    # then there are none, and the link is still there
    result = audit(_document())
    assert result.external_loads() == []
    assert result.anchors() == ["https://tickets.example/1"]


@pytest.mark.parametrize(
    "offending",
    [
        '<link rel="stylesheet" href="https://cdn.example/report.css">',
        '<script src="https://cdn.example/widget.js"></script>',
        '<img src="https://example.com/wafer.png">',
        "<style>@import url('https://cdn.example/x.css');</style>",
        '<div style="background: url(https://example.com/bg.png)">x</div>',
    ],
)
def test_the_audit_should_flag_an_external_load(offending: str) -> None:
    # given a document that does fetch something
    # when the audit runs
    # then it objects, so that the test above isn't passing because the audit is broken
    assert audit(f"<html><body>{offending}</body></html>").external_loads() != []

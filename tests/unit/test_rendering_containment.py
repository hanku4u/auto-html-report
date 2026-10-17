"""Containment: the document loads nothing from the network.

This is acceptance criterion 10, and it is the one criterion that is easy to satisfy by accident and
easy to break by accident: a block author pastes in a stylesheet link, or an ``<img src="https://...">``
from an API response, and the report quietly stops working the moment it is opened somewhere without
the network -- or, worse, leaks a request to whoever opens it.

The rule from ``docs/06-output-html.md`` §4.1 is precise, and deliberately not "no external URLs":

* nothing may *load* an external resource -- script, stylesheet, image, frame, font;
* an **anchor** to an external URL is fine, and is legitimate cell content. A report that links to a
  defect tracker is a report doing its job.

The auditing machinery lives in ``tests/support/html_audit.py``, where it is shared with the
stylesheet test in ``test_rendering_shell.py``.
"""

from __future__ import annotations

import pytest

from auto_inform.domain.block import BlockStatus
from auto_inform.rendering.cells import error_cell_html
from auto_inform.rendering.escaping import html_table
from auto_inform.rendering.shell import render_report
from tests.support.builders import make_outcome, make_report, make_row
from tests.support.html_audit import audit


def _representative_report(*, with_anchor: bool) -> str:
    """A report using the whole feature set: tables, figures, an error cell, optionally an anchor."""
    link = '<a href="https://tickets.example/INC-4242">INC-4242</a>' if with_anchor else "no link"
    figure = '<img class="ai-figure" src="data:image/png;base64,iVBORw0KGgo=" alt="Wafer map">'
    return render_report(
        make_report(
            make_row("Maintenance history — T-104", html_table(headers=["Date"], rows=[["today"]])),
            make_row("Wafer map — LOT-88213", figure),
            make_row("Ticket", link),
            make_row(
                "Broken",
                error_cell_html(
                    make_outcome(
                        "Broken",
                        BlockStatus.FAILED,
                        error_type="HTTPError",
                        error_message="upstream said <no>",
                    )
                ),
                status=BlockStatus.FAILED,
            ),
            title="LINE 4 — Daily Health",
            subtitle="Failed wafer — EVT-88213",
            metadata={"Lot": "LOT-88213"},
        )
    )


def test_rendered_document_should_load_no_external_resource() -> None:
    # given a report exercising tables, a figure and an error cell
    document = _representative_report(with_anchor=False)

    # when every reason to make a request is collected
    # then there are none: the document is one self-contained file
    assert audit(document).external_loads() == []


def test_rendered_document_with_an_external_anchor_should_still_be_contained() -> None:
    # given the same report, now with a link to a ticket system
    document = _representative_report(with_anchor=True)

    # when the audit runs
    # then it passes, and the anchor survives: the rule forbids resource loads, not URLs, and an
    # over-broad rule would fail a perfectly good report for citing its own defect tracker
    assert audit(document).external_loads() == []
    assert "https://tickets.example/INC-4242" in audit(document).anchors()


def test_rendered_document_should_embed_figures_rather_than_reference_them() -> None:
    # given a figure in a report
    document = _representative_report(with_anchor=False)

    # when its images are inspected
    # then every one is a data URI, so the picture travels with the file
    images = [
        attributes
        for tag, attributes in audit(document).tags
        if tag == "img" and "src" in attributes
    ]
    assert images, "the fixture should contain a figure"
    assert all(image["src"].startswith("data:") for image in images)


def test_rendered_stylesheet_should_be_scanned_without_its_own_comments() -> None:
    # given a document whose inlined stylesheet *documents* the no-@import rule in a comment
    document = _representative_report(with_anchor=False)

    # when the audit runs
    # then it does not flag the documentation -- an earlier version of the audit did exactly that,
    # which is why comments are stripped before scanning
    assert "@import" in document, "the fixture's CSS comments should mention the rule"
    assert audit(document).external_loads() == []


@pytest.mark.parametrize(
    "offending",
    [
        '<link rel="stylesheet" href="https://cdn.example/report.css">',
        '<script src="https://cdn.example/widget.js"></script>',
        '<img src="https://example.com/wafer.png">',
        '<iframe src="https://example.com/embed"></iframe>',
        '<video src="https://example.com/clip.mp4"></video>',
        "<style>@import url('https://cdn.example/x.css');</style>",
        '<div style="background: url(https://example.com/bg.png)">x</div>',
        '<img srcset="https://example.com/a.png 1x, https://example.com/b.png 2x">',
    ],
)
def test_the_containment_audit_should_flag_an_external_load(offending: str) -> None:
    # given a document that does fetch something -- one case per way of doing it
    document = f"<html><body>{offending}</body></html>"

    # when the audit runs
    # then it objects
    #
    # A negative control for the audit itself. Without it, a typo that made `external_loads` return an
    # empty list unconditionally would leave the two tests above passing while guarding nothing.
    assert audit(document).external_loads() != []


@pytest.mark.parametrize(
    "permitted",
    [
        '<a href="https://tickets.example/INC-1">a ticket</a>',
        '<a href="/relative/path">a relative anchor</a>',
        '<a href="#section">a same-page anchor</a>',
        '<img src="data:image/png;base64,AAAA">',
    ],
)
def test_the_containment_audit_should_permit_anchors_and_embedded_data(permitted: str) -> None:
    # given a document that references URLs without loading them
    document = f"<html><body>{permitted}</body></html>"

    # when the audit runs
    # then it is content: an anchor is a citation, not a fetch
    assert audit(document).external_loads() == []

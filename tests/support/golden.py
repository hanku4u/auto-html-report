"""The golden fixture and its normalisation.

The snapshot is stored as *real rendered output*, pretty-printed so that a reviewer can read the diff,
with exactly one thing masked: the footer's version token. Masking it is what stops a release from
breaking the comparison, which is how golden files rot -- somebody loosens the match until it asserts
nothing.

The regex targets the footer rather than every occurrence of the tool name, because the inlined
stylesheet mentions ``auto_inform`` in a comment, and masking that would quietly corrupt the CSS in the
snapshot.
"""

from __future__ import annotations

import re
from pathlib import Path

from auto_inform.domain.block import BlockStatus
from auto_inform.domain.report import Report
from auto_inform.rendering.escaping import html_table
from auto_inform.rendering.shell import render_report
from tests.support.builders import make_report, make_row

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden" / "report.html"

_FOOTER_VERSION = re.compile(r'(<footer class="ai-footer">auto_inform )\S+(</footer>)')
_WHITESPACE = re.compile(r"\s+")
_BETWEEN_TAGS = re.compile(r">\s+<")

#: A tiny but real PNG, so the snapshot stays readable. Base64 of a 1x1 transparent image.
_PLACEHOLDER_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def normalise_document(html: str) -> str:
    """Mask the version token and normalise insignificant whitespace, and nothing else.

    Whitespace *between tags* is genuinely insignificant in HTML, so it is removed outright; runs
    inside text collapse to a single space. Doing only the latter was an earlier mistake: it left
    reindenting a template looking like a change to the contract, which defeats the purpose of a golden
    file. Everything else is compared exactly, or the snapshot would not be worth committing.
    """
    without_version = _FOOTER_VERSION.sub(r"\1<version>\2", html)
    return _WHITESPACE.sub(" ", _BETWEEN_TAGS.sub("><", without_version)).strip()


def golden_report() -> Report:
    """The fixture: three blocks covering the document's header, rows, cell types and footer.

    Three rather than two. ``docs/06-output-html.md`` says the golden test is what makes §6 --
    including the error cell -- executable, and two healthy blocks would pin §3 and §7 while leaving
    §6 untested by the snapshot.
    """
    return make_report(
        make_row(
            "Maintenance history — T-104",
            html_table(
                headers=["Date", "Engineer", "Summary"],
                rows=[
                    ("2026-10-15", "A. Rivera", "Replaced thermocouple"),
                    ("2026-10-02", "J. Okafor", "Cleaned chamber"),
                ],
            ),
        ),
        make_row(
            "Wafer map — LOT-88213",
            f'<img class="ai-figure" src="data:image/png;base64,{_PLACEHOLDER_PNG}" '
            'alt="Wafer bin map for LOT-88213">',
        ),
        make_row(
            "Trend chart — yield",
            status=BlockStatus.FAILED,
            duration_seconds=61.2,
            attempts=3,
            error_type="TimeoutError",
            error_message="trend service did not respond",
        ),
        title="LINE 4 — Daily Health",
        subtitle="Failed wafer — EVT-88213",
        metadata={"Lot": "LOT-88213", "Tool": "T-104"},
    )


def golden_document_text() -> str:
    """The snapshot to commit: real output, with only the version token masked."""
    return _FOOTER_VERSION.sub(r"\1<version>\2", render_report(golden_report()))


def write_golden() -> None:
    """Regenerate the committed snapshot. Review the diff; do not run this to make tests pass."""
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(golden_document_text(), encoding="utf-8")

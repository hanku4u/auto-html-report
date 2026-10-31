"""The golden-file snapshot of a rendered report.

The point is to make the output contract in ``docs/06-output-html.md`` executable: if the markup moves,
this fails and somebody has to say out loud that the document changed shape. Two normalisations keep it
useful rather than brittle:

* insignificant whitespace, because reindenting the template is not a change to the contract;
* the footer's version token, because otherwise every release would break the comparison and somebody
  would eventually "fix" it by loosening the match until it asserted nothing.

Regenerate deliberately after reviewing the diff::

    uv run python -c "from tests.support.golden import write_golden; write_golden()"
"""

from __future__ import annotations

import re
from pathlib import Path

from auto_html_report.rendering.shell import render_report
from tests.support.golden import golden_report, normalise_document

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden" / "report.html"


def test_render_report_for_the_golden_fixture_should_match_the_golden_document() -> None:
    # given a fixture of three blocks -- a table, a figure and a failed block -- with fixed run id and
    # generation time
    document = render_report(golden_report())

    # when the rendered document is compared with the committed snapshot
    expected = GOLDEN_PATH.read_text(encoding="utf-8")

    # then they agree once whitespace and the version token are normalised away
    assert normalise_document(document) == normalise_document(expected)


def test_the_golden_document_should_exercise_the_contract_not_just_the_happy_path() -> None:
    # given the golden document
    document = GOLDEN_PATH.read_text(encoding="utf-8")

    # when it is inspected for the parts of the contract worth snapshotting
    # then it covers the header, the three column headings, a data table, an embedded figure, an error
    # cell and the footer -- a golden file that only showed a plain row would pin almost nothing
    assert '<th scope="col">Check</th>' in document
    assert '<table class="ai-data-table">' in document
    assert "data:image/png;base64," in document
    assert 'class="ai-error"' in document
    assert "ai-cell--error" in document
    assert "<footer" in document


def test_normalising_document_should_remove_the_version_token_and_collapse_whitespace() -> None:
    # given two documents differing only in version and whitespace
    first = '<html><body><footer class="ai-footer">auto_html_report 0.1.0</footer></body></html>'
    second = '<html>\n  <body>\n<footer class="ai-footer">auto_html_report 9.9.9</footer>\n</body></html>'

    # when both are normalised
    # then they are equal, which is exactly the tolerance the golden comparison needs and no more
    assert normalise_document(first) == normalise_document(second)
    assert normalise_document(first).count("auto_html_report") == 1


def test_normalising_document_should_still_notice_a_changed_structure() -> None:
    # given two documents differing in something that is not whitespace or a version
    first = "<html><body><h1>Title</h1></body></html>"
    second = "<html><body><h2>Title</h2></body></html>"

    # when both are normalised
    # then they are still different, so the normalisation has not thrown away the signal
    assert normalise_document(first) != normalise_document(second)


def test_the_golden_document_should_not_contain_a_hard_coded_version() -> None:
    # given the committed snapshot
    raw = GOLDEN_PATH.read_text(encoding="utf-8")

    # when the footer is examined
    # then the version is a placeholder, not a real number: a real one would pin the snapshot to a
    # release and break the suite on the next bump
    assert re.search(r'<footer class="ai-footer">auto_html_report <version></footer>', raw)

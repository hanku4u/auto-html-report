"""Link and anchor integrity across the document set.

Automates the audit that caught two broken anchors earlier in the project: a link whose heading had been
renamed, and one whose anchor never existed. Both were invisible to every other check, because a broken
anchor in Markdown fails silently -- the link just lands at the top of the page.

**Fences are skipped for links as well as headings.** A Python comment in an example starts with ``#``
and would otherwise read as a heading; symmetrically, a document about reports will sooner or later show
a markdown link inside an example, and auditing that as a real link would fail for no reason. Both
regexes therefore run over the same list of lines, rather than one of them over the whole file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*```")


def _lines_outside_fences(path: Path) -> list[str]:
    """Every line of a document that is not inside a fenced code block."""
    kept: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return kept


def _headings(path: Path) -> list[str]:
    """Every heading in a document, in order, excluding anything inside a fenced code block."""
    headings: list[str] = []
    for line in _lines_outside_fences(path):
        match = _HEADING.match(line)
        if match:
            headings.append(match.group(1))
    return headings


def anchor_for(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation stripped, *each* space to a hyphen.

    Each whitespace character becomes its own hyphen rather than runs collapsing into one. That is what
    produces the double hyphen in ``3-render--the-pure-phase``: the em dash is dropped as punctuation and
    the two spaces that surrounded it each become a hyphen. Getting this wrong makes an audit that
    disagrees with the links actually written, which is worse than no audit.
    """
    without_punctuation = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s", "-", without_punctuation).strip("-")


def anchors_for(headings: Sequence[str]) -> set[str]:
    """Every anchor a document's headings define, including GitHub's duplicates suffix.

    GitHub appends ``-1``, ``-2`` and so on to a heading that repeats, so a document with two identical
    headings defines both ``#the-heading`` and ``#the-heading-1``. It also checks each candidate against
    every anchor already generated, so a heading literally named ``A-1`` alongside two ``A``s becomes
    ``a-1-1`` rather than colliding with the suffix. Ignoring either rule would make this audit report a
    working link as broken -- the one failure direction it must never have.
    """
    anchors: set[str] = set()
    for heading in headings:
        base = anchor_for(heading)
        candidate, counter = base, 1
        while candidate in anchors:
            candidate = f"{base}-{counter}"
            counter += 1
        anchors.add(candidate)
    return anchors


def broken_links(docs: Path) -> list[str]:
    """Every link in the document set that points at a missing file or a missing heading."""
    problems: list[str] = []

    for path in sorted(docs.glob("*.md")):
        for target in _LINK.findall("\n".join(_lines_outside_fences(path))):
            if target.startswith(("http://", "https://", "mailto:")):
                continue

            file_part, _, anchor = target.partition("#")
            destination = (path.parent / file_part).resolve() if file_part else path

            if file_part and not destination.exists():
                problems.append(f"{path.name} -> {target}: no such file")
                continue

            if not anchor or destination.suffix != ".md":
                continue
            if anchor not in anchors_for(_headings(destination)):
                problems.append(f"{path.name} -> {target}: no such heading")

    return problems


def test_every_link_in_the_document_set_should_resolve() -> None:
    # given every cross-reference between the specification documents
    problems = broken_links(DOCS)

    # when each is resolved against the file and heading it names
    # then none is broken. Markdown fails silently here -- the reader simply lands at the top of the
    # page -- so nothing else would ever notice
    assert problems == []


def test_the_link_audit_should_ignore_links_inside_code_fences(tmp_path: Path) -> None:
    # given a document that *shows* a link inside a fenced example, as a spec about reports reasonably
    # might, pointing at a file that does not exist
    (tmp_path / "source.md").write_text(
        "Real link: [target](target.md).\n\n```markdown\n[an example](does-not-exist.md)\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "target.md").write_text("## A heading\n", encoding="utf-8")

    # when the audit runs
    # then the example is not audited as a link, because it is documentation of link syntax rather than
    # a reference -- and the real link still is
    assert broken_links(tmp_path) == []


def test_the_link_audit_should_flag_a_missing_anchor(tmp_path: Path) -> None:
    # given a document linking to a heading that does not exist
    (tmp_path / "target.md").write_text("## Present heading\n", encoding="utf-8")
    (tmp_path / "source.md").write_text(
        "See [the missing part](target.md#absent-heading) and [nothing at all](absent.md).",
        encoding="utf-8",
    )

    # when the audit runs
    # then it objects to both, so a bug that made the audit always return an empty list cannot pass
    # the test above while guarding nothing
    problems = broken_links(tmp_path)

    assert len(problems) == 2
    assert any("no such heading" in problem for problem in problems)
    assert any("no such file" in problem for problem in problems)


def test_the_link_audit_should_resolve_a_real_anchor_and_ignore_code_fences(tmp_path: Path) -> None:
    # given a document whose target has a heading, and a code block that merely looks like one
    (tmp_path / "target.md").write_text(
        "## The real heading\n\n```python\n# Not a heading\n```\n", encoding="utf-8"
    )
    (tmp_path / "source.md").write_text(
        "See [it](target.md#the-real-heading) and [the comment](target.md#not-a-heading).\n",
        encoding="utf-8",
    )

    # when the audit runs
    problems = broken_links(tmp_path)

    # then the real heading resolves and the comment inside the fence does not count as one
    assert len(problems) == 1
    assert "not-a-heading" in problems[0]


def test_the_link_audit_should_resolve_a_suffixed_anchor_for_a_repeated_heading(
    tmp_path: Path,
) -> None:
    # given a document with two identical headings, which GitHub distinguishes with a suffix
    (tmp_path / "target.md").write_text(
        "## Open questions\n\nfirst\n\n## Open questions\n\nsecond\n", encoding="utf-8"
    )
    (tmp_path / "source.md").write_text(
        "See [the second one](target.md#open-questions-1).\n", encoding="utf-8"
    )

    # when the audit runs
    # then the suffixed anchor resolves: ignoring GitHub's disambiguation would report a working link as
    # broken, which is the one direction this audit must never fail in
    assert broken_links(tmp_path) == []


def test_anchors_for_should_disambiguate_repeated_headings() -> None:
    # given a page whose headings repeat
    anchors = anchors_for(["Open questions", "Other", "Open questions", "Open questions"])

    # when the anchors are derived
    # then the repeats gain GitHub's numeric suffixes rather than overwriting each other
    assert anchors == {"open-questions", "other", "open-questions-1", "open-questions-2"}


def test_anchors_for_should_not_let_a_suffix_collide_with_a_naturally_named_heading() -> None:
    # given a heading literally named the way a repeated heading's suffix would be spelled
    anchors = anchors_for(["A", "A", "A-1"])

    # then all three keep distinct anchors. A counter that never consulted what already existed would
    # produce {a, a-1} and lose the third, reporting a working link to #a-1-1 as broken
    assert anchors == {"a", "a-1", "a-1-1"}


def test_anchors_for_with_no_repeats_should_yield_the_plain_anchors() -> None:
    # given headings that all differ
    # then no suffix appears, so the common case stays obvious
    assert anchors_for(["First", "Second"]) == {"first", "second"}


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("4. The trust boundary", "4-the-trust-boundary"),
        ("3. `render()` — the pure phase", "3-render--the-pure-phase"),
        ("6.4 Error cell", "64-error-cell"),
        ('4.1 What "self-contained" means precisely', "41-what-self-contained-means-precisely"),
        ("Open questions", "open-questions"),
    ],
)
def test_anchor_generation_should_match_the_anchors_used_in_the_documents(
    heading: str, expected: str
) -> None:
    # given headings and the anchors the documents link to
    # when the anchor is derived
    # then it matches, so the audit agrees with the links actually written -- including the em-dash case,
    # which produces a double hyphen and is easy to get wrong by hand
    assert anchor_for(heading) == expected

"""Link and anchor integrity across the document set.

Automates the audit that caught two broken anchors earlier in the project: a link whose heading had been
renamed, and one whose anchor never existed. Both were invisible to every other check, because a broken
anchor in Markdown fails silently -- the link just lands at the top of the page.

Headings are collected outside fenced code blocks, because a Python comment in an example starts with
``#`` and would otherwise be read as a heading.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*```")


def _headings(path: Path) -> list[str]:
    """Every heading in a document, excluding anything inside a fenced code block."""
    headings: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            headings.append(match.group(1))
    return headings


def anchor_for(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation stripped, *each* space to a hyphen.

    Each whitespace character becomes its own hyphen rather than runs collapsing into one. That is what
    produces the double hyphen in ``3-render--the-pure-phase``: the em dash is dropped as punctuation
    and the two spaces that surrounded it each become a hyphen. Getting this wrong makes an audit that
    disagrees with the links actually written, which is worse than no audit.
    """
    without_punctuation = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s", "-", without_punctuation).strip("-")


def broken_links(docs: Path) -> list[str]:
    """Every link in the document set that points at a missing file or a missing heading."""
    problems: list[str] = []

    for path in sorted(docs.glob("*.md")):
        for target in _LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue

            file_part, _, anchor = target.partition("#")
            destination = (path.parent / file_part).resolve() if file_part else path

            if file_part and not destination.exists():
                problems.append(f"{path.name} -> {target}: no such file")
                continue
            if anchor and destination.suffix == ".md":
                anchors = {anchor_for(heading) for heading in _headings(destination)}
                if anchor not in anchors:
                    problems.append(f"{path.name} -> {target}: no such heading")

    return problems


def test_every_link_in_the_document_set_should_resolve() -> None:
    # given every cross-reference between the specification documents
    problems = broken_links(DOCS)

    # when each is resolved against the file and heading it names
    # then none is broken. Markdown fails silently here -- the reader simply lands at the top of the
    # page -- so nothing else would ever notice
    assert problems == []


def test_the_link_audit_should_flag_a_missing_anchor(tmp_path: Path) -> None:
    # given a document linking to a heading that does not exist
    (tmp_path / "target.md").write_text("## Present heading\n", encoding="utf-8")
    (tmp_path / "source.md").write_text(
        "See [the missing part](target.md#absent-heading) and [nothing at all](absent.md).",
        encoding="utf-8",
    )

    # when the audit runs
    problems = broken_links(tmp_path)

    # then it objects to both, so a bug that made the audit always return an empty list cannot pass
    # the test above while guarding nothing
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

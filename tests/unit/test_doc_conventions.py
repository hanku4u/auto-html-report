"""The document-set conventions, enforced.

Every check here was previously done by hand, repeatedly, with ``diff`` and ``grep``: is the
``pyproject.toml`` snippet in the spec still the real file, does the index still quote the right ADR
range, has a document lost its frontmatter, has one crossed the size threshold without gaining a Dev
plan. Hand-checks get skipped on a busy day; these do not.

They are also the checks that have already caught real defects. The snippet comparison caught a CI
command that could never have passed, and the anchor audit in ``test_doc_links.py`` caught two broken
cross-references.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
README = DOCS / "README.md"

#: Documents above this many lines must carry a Dev plan, unless exempted below.
DEV_PLAN_THRESHOLD = 250

#: Append-only reference documents that specify no build work, exempted by docs/README.md. A decision
#: log grows without bound and has nothing to stage, so demanding a plan of it is absurdity rather than
#: rigour.
DEV_PLAN_EXEMPT = frozenset({"10-decision-log.md"})

KNOWN_STATUSES = frozenset({"proposed", "active", "superseded"})

_FENCE = re.compile(r"^```(\w*)\s*$", re.MULTILINE)


def _numbered_docs() -> list[Path]:
    return sorted(DOCS.glob("[0-9][0-9]-*.md"))


def _frontmatter(path: Path) -> dict[str, str]:
    """The YAML frontmatter of a document, as a flat mapping. Empty when there is none."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    try:
        end = text.index("\n---", 4)
    except ValueError:
        return {}
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def _fenced_blocks(path: Path, language: str) -> list[str]:
    """Every fenced code block of one language in a document."""
    text = path.read_text(encoding="utf-8")
    blocks: list[str] = []
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        match = _FENCE.match(lines[index])
        if match and match.group(1) == language:
            body: list[str] = []
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            blocks.append("".join(body))
        index += 1
    return blocks


# --- frontmatter and numbering ------------------------------------------------


def test_every_numbered_document_should_carry_status_and_description() -> None:
    # given every numbered document
    missing: dict[str, list[str]] = {}
    for path in _numbered_docs():
        fields = _frontmatter(path)
        absent = [key for key in ("status", "description") if not fields.get(key)]
        if absent:
            missing[path.name] = absent

    # when their frontmatter is inspected
    # then each declares both. `description` is what the index is built from and what a reader scanning
    # the directory decides on; without `status`, no reviewer can tell a draft from a ratified spec
    assert missing == {}


def test_every_numbered_document_should_declare_a_known_status() -> None:
    # given the three statuses the conventions allow
    unexpected = {
        path.name: _frontmatter(path)["status"]
        for path in _numbered_docs()
        if _frontmatter(path).get("status") not in KNOWN_STATUSES
    }

    # when each document's status is checked
    # then it is one of them, so tooling never has to guess at a fourth value
    assert unexpected == {}


def test_document_numbering_should_be_unique_and_contiguous() -> None:
    # given the numbered documents on disk
    numbers = [int(path.name[:2]) for path in _numbered_docs()]

    # when the sequence is inspected
    # then it starts at 1 and has no gaps or repeats: a gap looks like a document was deleted, and a
    # repeat means two files claim one number
    assert numbers == list(range(1, len(numbers) + 1))


def test_the_next_number_stated_in_the_index_should_be_the_actual_next_number() -> None:
    # given the index's claim about the next number to use
    text = README.read_text(encoding="utf-8")
    stated = re.search(r"next unused number for a new document is \*\*(\d+)\*\*", text)

    # when it is compared with the documents on disk
    expected = len(_numbered_docs()) + 1

    # then they agree, so a contributor following the convention does not collide with an existing file
    assert stated is not None, "docs/README.md no longer states the next unused number"
    assert int(stated.group(1)) == expected


# --- cross-document consistency ----------------------------------------------


def test_the_pyproject_snippet_should_be_the_real_pyproject_toml() -> None:
    # given the specification's copy of the project configuration
    blocks = _fenced_blocks(DOCS / "08-packaging.md", "toml")

    # when it is compared with the file it documents
    assert len(blocks) == 1, (
        "expected exactly one toml block in 08; a second one could drift unnoticed"
    )
    assert blocks[0] == (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"), (
        "docs/08-packaging.md §3 and pyproject.toml have drifted apart"
    )


@pytest.mark.parametrize("document", ["README.md", "10-decision-log.md"])
def test_the_adr_range_quoted_in_a_document_should_match_the_decision_log(document: str) -> None:
    # given a document that quotes the ADR range
    text = (DOCS / document).read_text(encoding="utf-8")
    quoted = re.search(r"ADR-0001 to ADR-(\d{4})", text)

    # when it is compared with the entries that actually exist
    entries = re.findall(
        r"^### ADR-(\d{4})", (DOCS / "10-decision-log.md").read_text(), re.MULTILINE
    )

    # then the range is accurate in both places. They are quoted in two files, so a new ADR added to one
    # and forgotten in the other is the likely drift
    assert quoted is not None, f"{document} no longer quotes an ADR range"
    assert entries == [f"{number:04d}" for number in range(1, len(entries) + 1)], "ADR numbering"
    assert quoted.group(1) == entries[-1]


# --- size and structure -------------------------------------------------------


def test_documents_over_the_size_threshold_should_carry_a_dev_plan() -> None:
    # given the documents large enough to need staging
    offenders: list[str] = []
    for path in _numbered_docs():
        if path.name in DEV_PLAN_EXEMPT:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > DEV_PLAN_THRESHOLD and "## Dev plan" not in path.read_text(encoding="utf-8"):
            offenders.append(f"{path.name} ({lines} lines)")

    # when each is checked for a Dev plan
    # then every oversized one has it, because a document too large to implement in one pass needs
    # review boundaries -- see docs/README.md, which also names the append-only exemption
    assert offenders == []


def test_the_dev_plan_exemption_should_still_refer_to_a_real_document() -> None:
    # given the documents exempted from the Dev plan rule
    existing = {path.name for path in _numbered_docs()}

    # when each exemption is resolved
    # then it names a file that exists: an exemption left behind after a document is deleted or renamed
    # would silently widen the rule
    assert DEV_PLAN_EXEMPT <= existing

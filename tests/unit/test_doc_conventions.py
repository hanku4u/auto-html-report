"""The document-set conventions, enforced.

Every check here was previously done by hand, repeatedly, with ``diff`` and ``grep``: is the
``pyproject.toml`` snippet in the spec still the real file, does the index still quote the right ADR
range, has a document lost its frontmatter, has one crossed the size threshold without gaining a Dev
plan. Hand-checks get skipped on a busy day; these do not.

They are also the checks that have already caught real defects. The snippet comparison caught a CI
command that could never have passed, and the anchor audit in ``test_doc_links.py`` caught two broken
cross-references.

**Every check takes the directory it audits as an argument, and every one has a control.** That is rule 7
of ``docs/09`` §2, and this module is where it earns its keep: most of the checks below are of the shape
"collect the offenders; assert there are none", and a broken file glob or frontmatter parser would return
an empty collection and pass. The controls at the bottom write a small offending document set to a
temporary directory and prove the check objects to it.
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


# --- the checks, each over a directory it is given ----------------------------


def numbered_documents(docs_dir: Path) -> list[Path]:
    """Every `NN-name.md` document in a directory, in order."""
    return sorted(docs_dir.glob("[0-9][0-9]-*.md"))


def frontmatter_of(path: Path) -> dict[str, str]:
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


def documents_missing_frontmatter(docs_dir: Path) -> dict[str, list[str]]:
    """``{document: absent fields}`` for every numbered document lacking ``status`` or ``description``."""
    missing: dict[str, list[str]] = {}
    for path in numbered_documents(docs_dir):
        fields = frontmatter_of(path)
        absent = [key for key in ("status", "description") if not fields.get(key)]
        if absent:
            missing[path.name] = absent
    return missing


def numbering_problems(docs_dir: Path) -> list[str]:
    """A description of any gap, repeat or disorder in the document numbering, or nothing."""
    numbers = [int(path.name[:2]) for path in numbered_documents(docs_dir)]
    expected = list(range(1, len(numbers) + 1))
    return [] if numbers == expected else [f"expected {expected}, found {numbers}"]


def documents_without_dev_plan(
    docs_dir: Path,
    *,
    threshold: int = DEV_PLAN_THRESHOLD,
    exempt: frozenset[str] = DEV_PLAN_EXEMPT,
) -> list[str]:
    """Every oversized document that is not exempt and carries no Dev plan."""
    offenders: list[str] = []
    for path in numbered_documents(docs_dir):
        if path.name in exempt:
            continue
        text = path.read_text(encoding="utf-8")
        lines = len(text.splitlines())
        if lines > threshold and "## Dev plan" not in text:
            offenders.append(f"{path.name} ({lines} lines)")
    return offenders


def fenced_blocks(path: Path, language: str) -> list[str]:
    """Every fenced code block of one language in a document."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks: list[str] = []
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


# --- the document set as it actually is ---------------------------------------


def test_the_document_discovery_should_find_the_numbered_documents() -> None:
    # given the documents on disk
    found = {path.name for path in numbered_documents(DOCS)}

    # when the discovery is inspected
    # then it sees the documents that exist. Every other check in this module iterates what discovery
    # returns, so a glob that silently found nothing would make most of them pass vacuously
    assert {"01-overview.md", "09-testing-and-acceptance.md"} <= found
    assert len(found) >= 10, sorted(found)


def test_every_numbered_document_should_carry_status_and_description() -> None:
    # given every numbered document
    # when their frontmatter is inspected
    # then each declares both. `description` is what the index is built from and what a reader scanning
    # the directory decides on; without `status`, no reviewer can tell a draft from a ratified spec
    assert documents_missing_frontmatter(DOCS) == {}


def test_every_numbered_document_should_declare_a_known_status() -> None:
    # given the three statuses the conventions allow
    unexpected = {
        path.name: frontmatter_of(path)["status"]
        for path in numbered_documents(DOCS)
        if frontmatter_of(path).get("status") not in KNOWN_STATUSES
    }

    # when each document's status is checked
    # then it is one of them, so tooling never has to guess at a fourth value
    assert unexpected == {}


def test_document_numbering_should_be_unique_and_contiguous() -> None:
    # given the numbered documents on disk
    problems = numbering_problems(DOCS)

    # when the sequence is inspected
    # then it starts at 1 and has no gaps or repeats: a gap looks like a document was deleted, and a
    # repeat means two files claim one number
    assert problems == []


def test_the_next_number_stated_in_the_index_should_be_the_actual_next_number() -> None:
    # given the index's claim about the next number to use
    text = README.read_text(encoding="utf-8")
    stated = re.search(r"next unused number for a new document is \*\*(\d+)\*\*", text)

    # when it is compared with the documents on disk
    expected = len(numbered_documents(DOCS)) + 1

    # then they agree, so a contributor following the convention does not collide with an existing file
    assert stated is not None, "docs/README.md no longer states the next unused number"
    assert int(stated.group(1)) == expected


def test_documents_over_the_size_threshold_should_carry_a_dev_plan() -> None:
    # given the documents large enough to need staging
    offenders = documents_without_dev_plan(DOCS)

    # then every oversized one has a Dev plan, because a document too large to implement in one pass
    # needs review boundaries -- see docs/README.md, which also names the append-only exemption
    assert offenders == []


def test_the_dev_plan_exemption_should_still_refer_to_a_real_document() -> None:
    # given the documents exempted from the Dev plan rule
    existing = {path.name for path in numbered_documents(DOCS)}

    # when each exemption is resolved
    # then it names a file that exists: an exemption left behind after a document is deleted or renamed
    # would silently widen the rule
    assert DEV_PLAN_EXEMPT <= existing


def test_the_pyproject_snippet_should_be_the_real_pyproject_toml() -> None:
    # given the specification's copy of the project configuration
    blocks = fenced_blocks(DOCS / "08-packaging.md", "toml")

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


# --- controls ------------------------------------------------------------------
#
# Each control writes the smallest document set that breaks one convention, and asserts the check for it
# objects. Without them, a check of the form "collect the offenders; assert there are none" would pass
# just as happily with a broken glob or parser as with a well-kept document set.


def test_the_frontmatter_check_should_flag_a_document_missing_a_field(tmp_path: Path) -> None:
    # given a document whose frontmatter omits one required field
    (tmp_path / "01-thing.md").write_text(
        "---\nstatus: proposed\n---\n\n# Thing\n", encoding="utf-8"
    )

    # when the check runs
    # then it names the document and the absent field
    assert documents_missing_frontmatter(tmp_path) == {"01-thing.md": ["description"]}


def test_the_frontmatter_check_should_flag_a_document_with_no_frontmatter_at_all(
    tmp_path: Path,
) -> None:
    # given a document with no frontmatter
    (tmp_path / "01-thing.md").write_text("# Thing\n\nNo frontmatter here.\n", encoding="utf-8")

    # when the check runs
    # then both required fields are reported missing, rather than the document being skipped
    assert documents_missing_frontmatter(tmp_path) == {"01-thing.md": ["status", "description"]}


def test_the_numbering_check_should_flag_a_gap(tmp_path: Path) -> None:
    # given documents numbered 01 and 03, with 02 missing
    for name in ("01-one.md", "03-three.md"):
        (tmp_path / name).write_text(
            "---\nstatus: proposed\ndescription: x\n---\n", encoding="utf-8"
        )

    # when the numbering is inspected
    # then the gap is reported
    assert numbering_problems(tmp_path) != []


def test_the_numbering_check_should_flag_a_repeat(tmp_path: Path) -> None:
    # given two documents claiming the same number
    for name in ("01-one.md", "01-also-one.md"):
        (tmp_path / name).write_text(
            "---\nstatus: proposed\ndescription: x\n---\n", encoding="utf-8"
        )

    # when the numbering is inspected
    # then the repeat is reported
    assert numbering_problems(tmp_path) != []


def test_the_dev_plan_check_should_flag_an_oversized_document_without_a_plan(
    tmp_path: Path,
) -> None:
    # given a document well over the threshold and carrying no Dev plan
    (tmp_path / "01-big.md").write_text("# Big\n" + "text\n" * 300, encoding="utf-8")

    # when the check runs against a low enough threshold to apply
    offenders = documents_without_dev_plan(tmp_path, threshold=10, exempt=frozenset())

    # then it objects
    assert offenders != []


def test_the_dev_plan_check_should_not_flag_an_oversized_document_that_has_one(
    tmp_path: Path,
) -> None:
    # given an oversized document that does carry a Dev plan, which is what the rule asks for
    (tmp_path / "01-big.md").write_text("# Big\n" + "text\n" * 300 + "## Dev plan\nstaged\n")

    # when the check runs
    # then it is content, so the control above is testing the rule rather than the file size
    assert documents_without_dev_plan(tmp_path, threshold=10, exempt=frozenset()) == []


def test_the_dev_plan_check_should_honour_the_exemption(tmp_path: Path) -> None:
    # given an oversized document that is exempt
    (tmp_path / "01-big.md").write_text("# Big\n" + "text\n" * 300, encoding="utf-8")

    # when the check runs with that document exempt
    # then it is skipped, which is what the exemption is for
    assert documents_without_dev_plan(tmp_path, threshold=10, exempt=frozenset({"01-big.md"})) == []

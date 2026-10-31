"""Every name the documents import from ``auto_html_report`` must actually be exported.

This guards a defect class that has now recurred three times: ``06 §4`` advertised rendering helpers the
package exported nothing of, ``05 §6`` imported five configuration factories that existed but were not on
the surface, and ``03 §2`` imported ``register_report`` while the registry held it but the root did not.
In each case a reader following the documentation got ``ImportError`` for a name that was real, just not
there.

The check is deliberately about the *documents'* claims rather than about ``__all__``: a test that walks
``__all__`` certifies whatever ``__all__`` happens to say, and was the reason the factory exports passed
review while being unusable.

**The allowlist shrinks.** Names belonging to a later stage are listed in ``PENDING`` with the stage that
will deliver them. A pending name that starts resolving fails the suite, so the list cannot quietly
become a place to park typos.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

import pytest

import auto_html_report

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
#: The README is the first document a reader runs code from, so its imports are checked too.
README = REPO_ROOT / "README.md"

#: Names the documents import that genuinely do not exist yet, each with the stage that lands it.
#: Empty since stage 9, and kept as the mechanism for the next one: remove an entry as soon as its stage
#: merges. test_pending_names_should_still_be_missing refuses an entry that has started resolving.
PENDING: dict[str, str] = {}

_PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _python_blocks(text: str) -> list[str]:
    return _PYTHON_BLOCK.findall(text)


def _imported_names(text: str, document: str) -> dict[str, set[str]]:
    """Every name imported from the package root in one document's Python examples.

    Returns name -> the documents that import it, so a failure can say where the reader was misled.
    Blocks that do not parse as Python are skipped: the documents contain fragments (a bare class body,
    an elided section) that were never meant to be a module.
    """
    found: dict[str, set[str]] = {}
    for block in _python_blocks(text):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "auto_html_report":
                for alias in node.names:
                    found.setdefault(alias.name, set()).add(document)
    return found


def _scan_documents(paths: Sequence[Path]) -> dict[str, set[str]]:
    """The union of the given documents' imports, keyed by name."""
    merged: dict[str, set[str]] = {}
    for path in paths:
        for name, documents in _imported_names(path.read_text(), path.name).items():
            merged.setdefault(name, set()).update(documents)
    return merged


def _documents() -> list[Path]:
    """Every document whose imports are checked: the specification set and the README."""
    return [*sorted(DOCS.glob("*.md")), README]


def test_every_name_the_documents_import_should_be_exported() -> None:
    # given every `from auto_html_report import ...` in the documentation set
    imported = _scan_documents(_documents())

    # when the scan is judged against the public surface
    missing = {
        name: sorted(documents)
        for name, documents in imported.items()
        if not hasattr(auto_html_report, name) and name not in PENDING
    }

    # then nothing is promised that a reader cannot import, and a failure names the document
    assert missing == {}, f"documented imports that do not resolve: {missing}"


def test_the_scan_should_actually_find_imports() -> None:
    # given the extractor, against the real documents
    imported = _scan_documents(_documents())

    # then it found a plausible surface -- the control that stops a broken regex from passing the test
    # above by finding nothing at all (docs/09 §2 rule 7)
    assert len(imported) >= 15, f"the extractor found only {sorted(imported)}"
    assert "Block" in imported
    assert "run_discovery" in imported


def test_the_extractor_should_find_names_in_a_synthetic_fence() -> None:
    # given a fenced block in the exact shape the documents use, plus a fence in another language
    text = (
        "Prose.\n\n```python\nfrom auto_html_report import Block, DiscoveryContext\n```\n\n"
        "```text\nfrom auto_html_report import NotPython\n```\n"
    )

    # when the real helpers scan it -- not a re-implementation of the walk, which would pass even if the
    # fence regex stopped matching
    blocks = _python_blocks(text)

    # then only the Python fence is read, and both of its names are found
    assert len(blocks) == 1
    assert _imported_names(text, "synthetic.md") == {
        "Block": {"synthetic.md"},
        "DiscoveryContext": {"synthetic.md"},
    }


def _assert_pending_missing(pending: dict[str, str]) -> None:
    """Fail for any entry that has started resolving."""
    for name, stage in pending.items():
        assert not hasattr(auto_html_report, name), (
            f"{name} now resolves; remove it from PENDING ({stage} has landed)"
        )


def test_pending_names_should_still_be_missing() -> None:
    # given the allowlist of names a later stage is expected to deliver (empty today, so this passes
    # vacuously -- but it is the control that fails the moment an entry outlives its stage)
    _assert_pending_missing(PENDING)


def test_the_pending_control_should_object_to_a_name_that_exists() -> None:
    # given an entry for a name that is on the surface -- the mistake the control exists to catch
    # when it is judged
    # then the control objects, so an entry cannot outlive its stage silently (docs/09 §2 rule 7)
    with pytest.raises(AssertionError, match="now resolves"):
        _assert_pending_missing({"Block": "stage 99"})


def test_the_scan_should_object_to_an_unknown_import_in_a_readme_shaped_file(
    tmp_path: Path,
) -> None:
    # given a README-shaped file importing a name the package does not export
    readme = tmp_path / "README.md"
    readme.write_text(
        "```python\nfrom auto_html_report import DefinitelyNotExported\n```\n", encoding="utf-8"
    )

    # when it is scanned by the same code that scans the real README
    imported = _scan_documents([readme])

    # then the missing name is found: the control that proves the README is really audited
    # (docs/09 §2 rule 7)
    missing = {name for name in imported if not hasattr(auto_html_report, name)}
    assert missing == {"DefinitelyNotExported"}

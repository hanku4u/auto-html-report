"""Every name the documents import from ``auto_inform`` must actually be exported.

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
from pathlib import Path

import pytest

import auto_inform

DOCS = Path(__file__).resolve().parents[2] / "docs"

#: Names the documents import that genuinely do not exist yet, each with the stage that lands it.
#: Remove an entry as soon as its stage merges -- test_pending_names_should_still_be_missing enforces it.
PENDING: dict[str, str] = {
    "exit_code_for": "stage 9",
    "run_processing": "stage 9",
}

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
            if isinstance(node, ast.ImportFrom) and node.module == "auto_inform":
                for alias in node.names:
                    found.setdefault(alias.name, set()).add(document)
    return found


def _scan_documents() -> dict[str, set[str]]:
    """The union of every document's imports, keyed by name."""
    merged: dict[str, set[str]] = {}
    for path in sorted(DOCS.glob("*.md")):
        for name, documents in _imported_names(path.read_text(), path.name).items():
            merged.setdefault(name, set()).update(documents)
    return merged


def test_every_name_the_documents_import_should_be_exported() -> None:
    # given every `from auto_inform import ...` in the documentation set
    imported = _scan_documents()

    # when the scan is judged against the public surface
    missing = {
        name: sorted(documents)
        for name, documents in imported.items()
        if not hasattr(auto_inform, name) and name not in PENDING
    }

    # then nothing is promised that a reader cannot import, and a failure names the document
    assert missing == {}, f"documented imports that do not resolve: {missing}"


def test_the_scan_should_actually_find_imports() -> None:
    # given the extractor, against the real documents
    imported = _scan_documents()

    # then it found a plausible surface -- the control that stops a broken regex from passing the test
    # above by finding nothing at all (docs/09 §2 rule 7)
    assert len(imported) >= 15, f"the extractor found only {sorted(imported)}"
    assert "Block" in imported
    assert "run_discovery" in imported


def test_the_extractor_should_find_names_in_a_synthetic_fence() -> None:
    # given a fenced block in the exact shape the documents use, plus a fence in another language
    text = (
        "Prose.\n\n```python\nfrom auto_inform import Block, DiscoveryContext\n```\n\n"
        "```text\nfrom auto_inform import NotPython\n```\n"
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


@pytest.mark.parametrize(("name", "stage"), sorted(PENDING.items()))
def test_pending_names_should_still_be_missing(name: str, stage: str) -> None:
    # given a name allowed to be absent because a later stage delivers it
    # when that stage has landed
    # then this fails, so the allowlist shrinks instead of becoming a permanent excuse
    assert not hasattr(auto_inform, name), (
        f"{name} now resolves; remove it from PENDING ({stage} has landed)"
    )

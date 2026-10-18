"""Markdown scanning shared by the documentation audits.

Both audits need the same thing before they can look at anything: the document's lines **outside** fenced
code blocks. A Python comment in an example starts with ``#`` and would otherwise read as a heading, and a
markdown link shown inside an example is documentation of link syntax rather than a reference.
"""

from __future__ import annotations

import re
from pathlib import Path

_FENCE = re.compile(r"^\s*```")


def lines_outside_fences(path: Path) -> list[str]:
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

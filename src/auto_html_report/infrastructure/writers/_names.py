"""The one filename rule every writer enforces.

A writer receives a complete name and never derives one (``docs/06-output-html.md`` §2). A name that is a
path — absolute, containing a separator, or ``..`` — would let a caller write outside the destination, so
both writers refuse it in the same words. Kept here rather than duplicated so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from auto_html_report.domain.errors import OutputError


def check_filename(filename: str) -> None:
    """Refuse anything that is not a plain file name in the writer's own destination."""
    if (
        not filename
        or filename in {".", ".."}
        or "\\" in filename
        # Control characters, including NUL: rejected on every platform, not only where a filesystem or a
        # key-value store happens to object. A backslash is a separator on Windows, where `Path.name` on a
        # POSIX host cannot see it.
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in filename)
        or Path(filename).name != filename
    ):
        raise OutputError(
            f"{filename!r} is not a plain filename. Writers receive a complete name and never derive a "
            "path; see docs/06-output-html.md §2."
        )

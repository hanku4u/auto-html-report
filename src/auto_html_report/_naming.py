"""Slug generation, shared by the things that need a filesystem- and attribute-safe token.

Two callers want this and they must not disagree: the row's ``data-block`` attribute
(``rendering/cells.py``) and the report filename (stage 6, ``docs/06-output-html.md`` §2). One
implementation, so a title that slugs cleanly for a filename slugs identically in the document.
"""

from __future__ import annotations

import re

#: Matches runs of anything that is not a lowercase letter or digit.
_NON_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")

#: The documented cap, chosen so that a filename built from a title, an event id and a timestamp
#: still fits comfortably inside filesystem and S3 key limits.
DEFAULT_MAX_LENGTH = 60


def slug(text: str, *, max_length: int = DEFAULT_MAX_LENGTH, fallback: str = "") -> str:
    """Return ``text`` as a lowercase, hyphen-separated token of at most ``max_length`` characters.

    ``fallback`` is returned when nothing survives -- a title consisting only of emoji or punctuation
    slugs to the empty string, and every caller needs *something* rather than a silently blank
    identifier. Callers pass their own fallback (``"block"``, ``"report"``) rather than each inventing
    a check.
    """
    if max_length < 1:
        raise ValueError(f"max_length must be at least 1, got {max_length}")

    slugified = _NON_SLUG_CHARACTERS.sub("-", text.lower()).strip("-")
    if len(slugified) > max_length:
        # Truncate, then strip again: cutting mid-word can leave a trailing hyphen.
        slugified = slugified[:max_length].rstrip("-")
    return slugified or fallback

"""Resolves the installed package version.

``pyproject.toml`` is the single source of truth. Nothing here stores a copy of it, because a second
copy is a copy that drifts -- the same reasoning that removed ``requirements.txt`` (ADR-0021).

Resolution is at runtime, and the not-installed path is real rather than hypothetical: running from
a source tree with the package not installed raises ``PackageNotFoundError``, which this module
converts into a placeholder rather than a guess. See ``docs/08-packaging.md`` §7 and ADR-0023.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

#: Distribution name, matching ``[project] name`` in pyproject.toml. Both the hyphen and underscore
#: spellings resolve, so this constant is not a portability trap whichever one a reader expects.
_DISTRIBUTION_NAME = "auto-inform"

#: Reported when the package is not installed. Deliberately not a plausible release number: a report
#: footer should never claim a version that was never built.
UNKNOWN_VERSION = "0+unknown"


def get_version() -> str:
    """Return the installed distribution's version, or :data:`UNKNOWN_VERSION` if not installed."""
    try:
        return version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION

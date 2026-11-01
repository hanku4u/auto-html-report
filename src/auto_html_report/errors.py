"""What can go wrong while building a report."""

from __future__ import annotations


class ReportError(Exception):
    """Base class, so a caller can catch every failure this package raises on purpose."""


class MissingContextError(ReportError):
    """A block needs a context key nobody supplied. Raised before anything is fetched."""


class FetchError(ReportError):
    """A gather function failed for good. The original exception is the ``__cause__``."""


class TransientError(Exception):
    """Raise from a gather function for a failure worth retrying, such as an HTTP 503."""

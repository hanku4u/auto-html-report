"""The exception hierarchy.

Every error this package raises derives from :class:`AutoHtmlReportError`, so a caller can catch one
name and be confident nothing was missed. See ``docs/02-block-authoring.md`` §4.

The split between :class:`TransientBlockError` and :class:`PermanentBlockError` is load-bearing: it
is what decides whether the framework retries a block. Getting it wrong in either direction is
expensive — retrying a 404 wastes the run's budget, and refusing to retry a 503 loses the report.
"""

from __future__ import annotations


class AutoHtmlReportError(Exception):
    """Base class for every error the framework *reports*.

    The hierarchy covers failures the framework itself diagnoses — a block that failed, a store that is
    unreachable, a report that could not be written. Plain Python errors for a broken contract are raised
    as themselves and deliberately not wrapped: a ``TypeError`` for an argument of the wrong type or a
    ``ValueError`` for an impossible value says exactly what is wrong, and wrapping it in a framework type
    would hide the mistake rather than classify it.
    """


class BlockError(AutoHtmlReportError):
    """A block failed to collect or render its data."""


class TransientBlockError(BlockError):
    """A failure that may succeed on a retry: 5xx, 429, connection reset, "try again"."""


class PermanentBlockError(BlockError):
    """A failure that will not improve on a retry: 404, a bad payload shape, auth failure."""


class ConfigurationError(AutoHtmlReportError):
    """Invalid settings, an invalid report definition, or a missing report factory."""


class StoreError(AutoHtmlReportError):
    """The event store is unreachable or corrupt."""


class OutputError(AutoHtmlReportError):
    """A report could not be written."""

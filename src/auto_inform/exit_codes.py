"""Exit codes for CI: the vocabulary the framework supplies and the ranking of a finished run.

There is no CLI (ADR-0001), so a job script returns one of these from ``main()``. The precedence table
in ``docs/05-discovery-and-processing.md`` §5 is the contract: the most severe condition wins, and the
codes are a small closed set because a CI system can only branch on so many numbers.

**Ranking reads recorded outcomes.** Both run types carry an ``error_type`` per outcome, and
:func:`exit_code_for` ranks those strings:

1. any ``ConfigurationError`` -- a factory missing or a definition refused, which means the deployment
   is wrong rather than the data;
2. any ``StoreError`` -- the store failed mid-run;
3. any ``OutputError`` -- a report was built but not written;
4. anything else that did not succeed -- a partial failure;
5. nothing -- success.

Because the runner records operational failures instead of raising (ADR-0030), the ranking sees them.
The failures that still escape happen before a rankable run exists: a ``pending()`` call that cannot read
the store, and discovery's ``save()`` after its concurrent phase. A job script that wants codes for those
catches them itself (``docs/05`` §6), and the exception carries the diagnosis.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_inform.application.discovery import DiscoveryRun
    from auto_inform.processing import ProcessingRun


class ExitCode(IntEnum):
    """What a job script should return, in decreasing order of severity."""

    SUCCESS = 0
    PARTIAL_FAILURE = 1
    CONFIGURATION_ERROR = 2
    STORE_ERROR = 3
    OUTPUT_ERROR = 4


#: Ordered by severity, highest first. A tuple rather than a chain of ``if``s so the ranking is data,
#: and a test can walk it rather than restating it.
_RANKING: tuple[tuple[str, ExitCode], ...] = (
    ("ConfigurationError", ExitCode.CONFIGURATION_ERROR),
    ("StoreError", ExitCode.STORE_ERROR),
    ("OutputError", ExitCode.OUTPUT_ERROR),
)


def exit_code_for(run: DiscoveryRun | ProcessingRun) -> ExitCode:
    """Map a finished run to a process exit code. Never raises for a run it was given."""
    error_types = {outcome.error_type for outcome in run.outcomes if outcome.error_type is not None}
    if not error_types:
        return ExitCode.SUCCESS

    for error_type, code in _RANKING:
        if error_type in error_types:
            return code

    # Anything else is a partial failure: reports were produced, but not all of them, or not cleanly.
    return ExitCode.PARTIAL_FAILURE

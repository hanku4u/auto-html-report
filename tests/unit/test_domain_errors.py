"""The exception hierarchy and the distinctions that drive retry behaviour.

Every error here is caught somewhere by type, so the shape of this hierarchy *is* behaviour.
"""

from __future__ import annotations

import pytest

from auto_html_report.domain.errors import (
    AutoHtmlReportError,
    BlockError,
    ConfigurationError,
    OutputError,
    PermanentBlockError,
    StoreError,
    TransientBlockError,
)


@pytest.mark.parametrize(
    "error_type",
    [
        BlockError,
        TransientBlockError,
        PermanentBlockError,
        ConfigurationError,
        StoreError,
        OutputError,
    ],
)
def test_every_documented_error_should_derive_from_auto_html_report_error(
    error_type: type[Exception],
) -> None:
    # given the documented error hierarchy
    # when a caller catches the base class
    # then every concrete error is caught, so a single except clause is always sufficient
    assert issubclass(error_type, AutoHtmlReportError)


@pytest.mark.parametrize("error_type", [TransientBlockError, PermanentBlockError])
def test_block_level_errors_should_also_be_block_errors(error_type: type[Exception]) -> None:
    # given the two classifications a block author chooses between
    # when a caller handles block failures generically
    # then both are caught, while the distinction is still available to the retry policy
    assert issubclass(error_type, BlockError)


@pytest.mark.parametrize("error_type", [ConfigurationError, StoreError, OutputError])
def test_operator_errors_should_not_be_block_errors(error_type: type[Exception]) -> None:
    # given the errors that abort a run rather than filling one cell
    # when a handler catches BlockError to render an error cell
    # then these are not swallowed by it: an unreadable store is an operator problem, not a red row,
    # and quietly rendering it as one would hide a broken deployment
    assert not issubclass(error_type, BlockError)


def test_transient_and_permanent_block_errors_should_be_distinct() -> None:
    # given the two retry classifications
    # when one is compared to the other
    # then neither is a subclass of the other, so a retry policy cannot mistake one for the other
    assert not issubclass(TransientBlockError, PermanentBlockError)
    assert not issubclass(PermanentBlockError, TransientBlockError)

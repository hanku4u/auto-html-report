"""The Block contract: naming, per-block overrides, and the outcome value type.

The stub blocks below also serve as a compile-time test: mypy verifies that a subclass annotating
its context parameters as ``ReportContext`` and ``RenderContext`` is a compatible override of the
ABC. That is what proves the type-only import in ``domain/block.py`` actually works.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from auto_html_report.application.context import RenderContext, ReportContext
from auto_html_report.domain.block import Block, BlockOutcome, BlockStatus


class _NamedByDisplayName(Block[int]):
    display_name = "From display_name"

    def collect(self, context: ReportContext) -> int:
        return 1

    def render(self, data: int, context: RenderContext) -> str:
        return str(data)


class _Unnamed(Block[int]):
    """No display_name and no name=, which must fail loudly rather than render a blank cell."""

    def collect(self, context: ReportContext) -> int:
        return 2

    def render(self, data: int, context: RenderContext) -> str:
        return ""


def test_block_with_display_name_only_should_use_it_as_item_text() -> None:
    # given a block class that sets display_name
    # when it is constructed without an explicit name
    # then that display_name is the Item text
    assert _NamedByDisplayName().name == "From display_name"


def test_block_with_explicit_name_should_override_display_name() -> None:
    # given a block that already has a display_name
    # when it is constructed with name=...
    # then the explicit name wins, which is how one block type fills several rows in one report
    block = _NamedByDisplayName(name="From display_name -- T-104")
    assert block.name == "From display_name -- T-104"


def test_block_with_neither_display_name_nor_explicit_name_should_raise_value_error() -> None:
    # given a block class that sets no display_name
    # when it is constructed without a name
    # then construction fails loudly instead of producing an unattributable Item cell
    with pytest.raises(ValueError, match="display_name"):
        _Unnamed()


def test_block_name_should_not_be_settable_after_construction() -> None:
    # given a constructed block
    block = _NamedByDisplayName()

    # when its name is reassigned
    # then it is refused: the name is part of the row's identity, and duplicate detection depends
    # on it not moving between definition and assembly
    with pytest.raises(AttributeError):
        block.name = "something else"  # type: ignore[misc]


def test_block_override_hooks_should_default_to_none_so_settings_decide() -> None:
    # given the ABC's per-block override hooks
    # when a block does not override them
    # then they are None, which means "use the framework default from Settings"
    assert _NamedByDisplayName.timeout_seconds is None
    assert _NamedByDisplayName.max_retries is None


def test_block_should_accept_its_own_timeout_and_retry_overrides() -> None:
    # given a block that overrides both hooks
    class _Impatient(Block[int]):
        display_name = "Impatient"
        timeout_seconds = 5.0
        max_retries = 0

        def collect(self, context: ReportContext) -> int:
            return 0

        def render(self, data: int, context: RenderContext) -> str:
            return ""

    # when the overrides are read off the class
    # then they are visible to the framework, which prefers them over the global defaults
    assert _Impatient.timeout_seconds == 5.0
    assert _Impatient.max_retries == 0


def test_block_status_members_should_be_the_three_documented_states() -> None:
    assert [status.value for status in BlockStatus] == ["OK", "FAILED", "TIMED_OUT"]


def test_block_outcome_should_be_frozen() -> None:
    # given a recorded outcome
    outcome = BlockOutcome(name="Stub", status=BlockStatus.OK, duration_seconds=1.5, attempts=1)

    # when a field is reassigned
    # then it is refused: outcomes are the audit trail of a run and must not be edited in place
    with pytest.raises(FrozenInstanceError):
        outcome.status = BlockStatus.FAILED  # type: ignore[misc]


def test_block_outcome_without_an_error_should_default_its_error_fields_to_none() -> None:
    # given a successful outcome built without error information
    outcome = BlockOutcome(name="Stub", status=BlockStatus.OK, duration_seconds=1.5, attempts=1)

    # when the error fields are read
    # then they are None, so "no error" needs no sentinel string
    assert outcome.error_type is None
    assert outcome.error_message is None


def test_block_outcome_should_carry_an_error_summary_safe_for_display() -> None:
    # given a failed block
    outcome = BlockOutcome(
        name="Stub",
        status=BlockStatus.FAILED,
        duration_seconds=61.2,
        attempts=3,
        error_type="TimeoutError",
        error_message="maintenance service did not respond",
    )

    # when the outcome is rendered into an error cell
    # then the type, message, attempt count and duration are all available, and the message is a
    # summary rather than a traceback
    assert (outcome.error_type, outcome.attempts, outcome.duration_seconds) == (
        "TimeoutError",
        3,
        61.2,
    )
    assert "\n" not in (outcome.error_message or "")

"""The collector: budgets, retries, isolation and ordering.

This is the done-when of stage 5, and the highest-risk module in the project, so the tests are written
around the failure modes rather than the happy path.

Every timing test runs against the ``FakeClock``: work is simulated with ``advance()`` (which moves time
without recording a sleep), and backoff is asserted from the recorded sleeps. Exactly one test waits on
real time -- the hang guard -- because that is the one place the framework deliberately does (ADR-0027).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import cast

import pytest

from auto_inform.application.collect_blocks import (
    _BACKSTOP_GRACE_SECONDS,
    _hang_guard_seconds,
    collect_blocks,
)
from auto_inform.application.context import RenderContext, ReportContext
from auto_inform.domain.block import Block, BlockStatus
from auto_inform.domain.errors import ConfigurationError, PermanentBlockError, TransientBlockError
from auto_inform.domain.report import ReportRow
from auto_inform.infrastructure.settings import Settings
from auto_inform.rendering.figures import figure_lock, figure_to_data_uri
from tests.support.clocks import FakeClock
from tests.support.contexts import make_report_context

TITLE = "Test report"


# --- blocks that exercise one behaviour each ----------------------------------


class _WorkBlock(Block[str]):
    """Simulates an amount of work by moving the fake clock, then renders its own name."""

    def __init__(self, *, name: str, work_seconds: float = 0.0) -> None:
        super().__init__(name=name)
        self._work = work_seconds

    def collect(self, context: ReportContext) -> str:
        # advance() rather than sleep(): this is work, not a wait, so it must not appear in the recorded
        # backoff schedule. Only the double exposes it; the port deliberately does not.
        cast(FakeClock, context.clock).advance(self._work)
        return self.name

    def render(self, data: str, context: RenderContext) -> str:
        return f"<p>{data}</p>"


class _FlakyBlock(Block[str]):
    """Fails a configured number of times, then succeeds."""

    def __init__(
        self, *, name: str, failures: int = 1, exception: BaseException | None = None
    ) -> None:
        super().__init__(name=name)
        self._remaining = failures
        self._exception = exception or TransientBlockError("try again")

    def collect(self, context: ReportContext) -> str:
        if self._remaining > 0:
            self._remaining -= 1
            raise self._exception
        return self.name

    def render(self, data: str, context: RenderContext) -> str:
        return f"<p>{data}</p>"


class _PermanentBlock(Block[str]):
    def collect(self, context: ReportContext) -> str:
        raise PermanentBlockError("this will not improve on a retry")

    def render(self, data: str, context: RenderContext) -> str:
        return ""


class _OverrunningBlock(Block[str]):
    """Calls collect() for longer than its own budget, then returns anyway."""

    timeout_seconds = 1.0

    def collect(self, context: ReportContext) -> str:
        cast(FakeClock, context.clock).advance(5.0)
        return "late"

    def render(self, data: str, context: RenderContext) -> str:
        return "<p>late</p>"


class _StuckBlock(Block[str]):
    """Stays inside collect() until released: the case only a real-time guard can catch."""

    def __init__(self, *, name: str, release: threading.Event) -> None:
        super().__init__(name=name)
        self._release = release

    def collect(self, context: ReportContext) -> str:
        self._release.wait(timeout=30)
        return self.name

    def render(self, data: str, context: RenderContext) -> str:
        return "<p>released</p>"


class _RaisingRenderBlock(Block[str]):
    def collect(self, context: ReportContext) -> str:
        return "fine"

    def render(self, data: str, context: RenderContext) -> str:
        raise RuntimeError("render bug")


class _TitleBlock(Block[str]):
    def collect(self, context: ReportContext) -> str:
        return "t"

    def render(self, data: str, context: RenderContext) -> str:
        return f"<p>{context.report_title}</p>"


class _FigureBlock(Block[str]):
    """Renders a real chart, following the documented figure pattern."""

    def collect(self, context: ReportContext) -> str:
        return self.name

    def render(self, data: str, context: RenderContext) -> str:
        from matplotlib import pyplot

        with figure_lock():
            figure, axes = pyplot.subplots(figsize=(2, 2))
            try:
                axes.plot([0, 1], [0, 1])
                uri = figure_to_data_uri(figure)
            finally:
                pyplot.close(figure)
        return f'<img class="ai-figure" src="{uri}" alt="{data}">'


def _collect(
    blocks: Sequence[Block[str]],
    *,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
) -> tuple[ReportRow, ...]:
    context = make_report_context(settings=settings, clock=clock)
    return collect_blocks(blocks, context=context, report_title=TITLE)


# --- ordering, rendering and isolation ----------------------------------------


def test_collect_blocks_should_return_one_row_per_block_in_argument_order() -> None:
    # given the slowest block first, so completion order is the reverse of argument order
    blocks = [
        _WorkBlock(name="Slowest", work_seconds=5.0),
        _WorkBlock(name="Faster", work_seconds=1.0),
        _WorkBlock(name="Fastest"),
    ]

    # when they are collected
    rows = _collect(blocks)

    # then the rows are in argument order: results are written into an index-ordered array as they
    # complete, rather than yielded as they finish
    assert [row.item for row in rows] == ["Slowest", "Faster", "Fastest"]


def test_collect_blocks_should_render_each_blocks_data() -> None:
    # given a block that renders its collected data
    rows = _collect([_WorkBlock(name="Row")])

    # then the row carries the block's HTML, the outcome records success, and the attempt count is one
    assert rows[0].html == "<p>Row</p>"
    assert rows[0].outcome.status is BlockStatus.OK
    assert rows[0].outcome.attempts == 1


def test_collect_blocks_with_a_failing_block_should_isolate_it_from_its_peers() -> None:
    # given a report where the middle block fails permanently
    blocks = [_WorkBlock(name="Before"), _PermanentBlock(name="Broken"), _WorkBlock(name="After")]

    # when they are collected
    rows = _collect(blocks)

    # then every block still produced a row, and the failure is confined to its own
    assert [row.outcome.status for row in rows] == [
        BlockStatus.OK,
        BlockStatus.FAILED,
        BlockStatus.OK,
    ]
    assert "Broken" not in rows[0].html


def test_collect_blocks_should_leave_a_failed_rows_html_empty_for_the_renderer() -> None:
    # given a failed block
    rows = _collect([_PermanentBlock(name="Broken")])

    # when the row is inspected
    # then the outcome carries the error detail and html is empty: the error cell is markup, and markup
    # is the renderer's job -- application/ may not import rendering/ (docs/01 §4 rule 2)
    assert rows[0].html == ""
    assert rows[0].outcome.error_type == "PermanentBlockError"
    assert rows[0].outcome.error_message == "this will not improve on a retry"


def test_collect_blocks_with_a_raising_render_should_isolate_it_too() -> None:
    # given a block whose render() raises, which is a code defect rather than a data condition
    rows = _collect([_RaisingRenderBlock(name="Buggy"), _WorkBlock(name="Fine")])

    # when it is collected
    # then the defect is confined to one cell instead of taking the report down
    assert [row.outcome.status for row in rows] == [BlockStatus.FAILED, BlockStatus.OK]
    assert rows[0].outcome.error_message == "render bug"


def test_collect_blocks_should_offer_the_report_title_to_each_blocks_render_context() -> None:
    # given a block that renders the report title
    rows = _collect([_TitleBlock(name="Title")])

    # then collection passed it through, so a block does not have to guess what it is rendering into
    assert rows[0].html == f"<p>{TITLE}</p>"


# --- retries ------------------------------------------------------------------


def test_collect_blocks_should_retry_a_transient_failure_then_succeed() -> None:
    # given a block that fails twice and then works
    clock = FakeClock()
    rows = _collect([_FlakyBlock(name="Flaky", failures=2)], clock=clock)

    # when it is collected
    # then it succeeded on the third attempt, and the outcome says so
    assert rows[0].outcome.status is BlockStatus.OK
    assert rows[0].outcome.attempts == 3
    assert len(clock.sleeps) == 2


def test_collect_blocks_should_back_off_exponentially_with_jitter() -> None:
    # given a base delay and two retries
    clock = FakeClock()
    settings = Settings(block_retries=2, retry_base_delay_seconds=1.0)

    # when a block fails twice and succeeds
    _collect([_FlakyBlock(name="Flaky", failures=2)], settings=settings, clock=clock)

    # then the sleeps follow base * 2**(attempt-1) plus up to base again, asserted as data rather than
    # by waiting. Bounds rather than exact values, because the jitter is random by design
    assert len(clock.sleeps) == 2
    first, second = clock.sleeps
    assert 1.0 <= first <= 2.0
    assert 2.0 <= second <= 3.0


def test_collect_blocks_should_not_retry_a_permanent_failure() -> None:
    # given a permanent failure and retries available
    clock = FakeClock()
    rows = _collect([_PermanentBlock(name="Broken")], clock=clock)

    # when it is collected
    # then it was attempted exactly once: retrying a 404 or a bad payload is a slower way to fail
    assert rows[0].outcome.attempts == 1
    assert clock.sleeps == ()


def test_collect_blocks_should_retry_an_exception_type_named_in_settings() -> None:
    # given a block raising KeyError and a configuration that calls KeyError transient
    clock = FakeClock()
    settings = Settings(block_retries=1, retryable_exceptions=("KeyError",))
    flaky = _FlakyBlock(name="Flaky", failures=1, exception=KeyError("missing"))

    # when it is collected
    # then the configured classification applied, so a project can retry its own transient error types
    rows = _collect([flaky], settings=settings, clock=clock)

    assert rows[0].outcome.status is BlockStatus.OK
    assert rows[0].outcome.attempts == 2


def test_collect_blocks_should_honour_a_blocks_zero_retry_override() -> None:
    # given a block that declares no retries for itself
    class _Impatient(_FlakyBlock):
        max_retries = 0

    clock = FakeClock()

    # when it fails transiently
    rows = _collect([_Impatient(name="Impatient", failures=1)], clock=clock)

    # then the block's own preference beats the configured default, and nothing was retried
    assert rows[0].outcome.status is BlockStatus.FAILED
    assert rows[0].outcome.attempts == 1
    assert clock.sleeps == ()


# --- budgets ------------------------------------------------------------------


def test_collect_blocks_should_mark_a_block_that_overran_its_own_budget_as_timed_out() -> None:
    # given a block with a one-second budget whose collect() takes five
    rows = _collect([_OverrunningBlock(name="Late"), _WorkBlock(name="Fine")])

    # when it is collected
    # then it is TIMED_OUT rather than OK -- the work came back, but too late to be usable -- and the
    # other block is unaffected. No retry: another attempt would overrun again
    assert rows[0].outcome.status is BlockStatus.TIMED_OUT
    assert rows[0].outcome.attempts == 1
    assert rows[0].outcome.error_type == "TimeoutError"
    assert rows[1].outcome.status is BlockStatus.OK


def test_collect_blocks_should_take_the_budget_from_settings_when_the_block_declares_none() -> None:
    # given a block with no override and a one-second global budget
    class _Slow(Block[str]):
        def collect(self, context: ReportContext) -> str:
            cast(FakeClock, context.clock).advance(5.0)
            return "late"

        def render(self, data: str, context: RenderContext) -> str:
            return ""

    rows = _collect([_Slow(name="Slow")], settings=Settings(block_timeout_seconds=1.0))

    # then the global budget applied
    assert rows[0].outcome.status is BlockStatus.TIMED_OUT


def test_collect_blocks_should_abandon_a_block_that_never_returns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # given a block wedged inside collect(), and a budget small enough to test cheaply
    release = threading.Event()
    settings = Settings(block_timeout_seconds=0.05, block_retries=0)
    caplog.set_level(logging.WARNING)

    try:
        # when it is collected
        rows = _collect(
            [_StuckBlock(name="Stuck", release=release), _WorkBlock(name="Fine")], settings=settings
        )
    finally:
        release.set()  # let the abandoned thread finish so it does not outlive the suite

    # then it is TIMED_OUT and the run continued. This is the one real-time test: a block that never
    # touches the clock cannot be caught by a clock-driven deadline (ADR-0027)
    assert rows[0].outcome.status is BlockStatus.TIMED_OUT
    assert rows[0].outcome.error_type == "TimeoutError"
    assert rows[1].outcome.status is BlockStatus.OK
    assert any(record.message == "block.abandoned" for record in caplog.records)


def test_the_hang_guard_should_allow_every_attempt_plus_the_worst_case_backoff() -> None:
    # given a block declaring a one-second budget, two retries and a one-second backoff base
    block = _OverrunningBlock(name="Late")
    context = make_report_context(settings=Settings(block_retries=2, retry_base_delay_seconds=1.0))

    # when the guard is computed
    # then it covers every attempt and the largest possible jitter, plus the grace period: three attempts
    # at 1 s, and a worst-case backoff of (1*2^0 + 1) then (1*2^1 + 1), so 5 s. Asserted as arithmetic
    # here so the integration test does not have to spend nine seconds proving it
    assert _hang_guard_seconds(block, context) == pytest.approx(
        1.0 * 3 + (1.0 + 1.0 + 2.0 + 1.0) + _BACKSTOP_GRACE_SECONDS
    )


# --- validation and logging ---------------------------------------------------


def test_collect_blocks_with_no_blocks_should_raise_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="at least one block"):
        _collect([])


def test_collect_blocks_with_duplicate_names_should_raise_configuration_error() -> None:
    # given two blocks that would render into the same Item cell
    blocks = [_WorkBlock(name="Same"), _WorkBlock(name="Same")]

    # then the set is refused before anything is collected
    with pytest.raises(ConfigurationError, match="duplicate block names"):
        _collect(blocks)


def test_collect_blocks_should_log_start_finish_and_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # given a healthy block and a failing one
    caplog.set_level(logging.INFO)

    # when they are collected
    _collect([_WorkBlock(name="Fine"), _PermanentBlock(name="Broken")])

    # then each block's lifecycle is in the log with the fields a slow report is diagnosed from
    messages = [record.message for record in caplog.records]
    assert "block.start" in messages
    assert "block.finish" in messages
    assert "block.failed" in messages
    finished = next(r for r in caplog.records if r.message == "block.finish")
    assert getattr(finished, "duration_ms", None) is not None
    assert getattr(finished, "status", None) == BlockStatus.OK.value


# --- the thread-safety test the plan calls for --------------------------------


def test_sixteen_blocks_half_rendering_figures_should_keep_their_order() -> None:
    # given sixteen blocks, every other one rendering a real chart
    blocks: list[Block[str]] = [
        _FigureBlock(name=f"Figure {index}") if index % 2 else _WorkBlock(name=f"Table {index}")
        for index in range(16)
    ]

    # when they run concurrently
    rows = _collect(blocks)

    # then nothing raised, every row succeeded, and the order is the argument order. This is the test
    # that catches a missing figure lock: without it, concurrent matplotlib work produces exceptions or
    # wrong images (ADR-0025)
    assert [row.item for row in rows] == [block.name for block in blocks]
    assert all(row.outcome.status is BlockStatus.OK for row in rows)

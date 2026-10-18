"""The shared execution rules: one implementation, used by blocks and discoverers.

These tests cover what belongs to the *mechanism* rather than to a caller: the hang-guard arithmetic
and the fact that an optional finalise step runs inside the worker but outside the retry loop. The
behaviour of the retry loop itself is pinned through ``test_collect_blocks.py`` (as a block) and
``test_discovery.py`` (as a discoverer), because that is how the framework actually reaches it.

Moved here from ``test_collect_blocks.py`` when the mechanics moved out of the collector: the guard is
not a property of blocks any more, and leaving its test behind would have claimed it was.
"""

from __future__ import annotations

import pytest

from auto_inform.application.execution import (
    _BACKSTOP_GRACE_SECONDS,
    BLOCK_EXECUTION,
    ExecutionStatus,
    Task,
    _hang_guard_seconds,
    execute_tasks,
)
from auto_inform.domain.errors import ConfigurationError
from auto_inform.infrastructure.settings import Settings
from tests.support.clocks import FakeClock
from tests.support.contexts import make_report_context


def test_the_hang_guard_should_allow_every_attempt_plus_the_worst_case_backoff() -> None:
    # given a task declaring a one-second budget, two retries and a one-second backoff base
    task: Task[object, object] = Task(
        name="Late", attempt=lambda _attempt: None, timeout_seconds=1.0, max_retries=2
    )
    settings = Settings(block_retries=2, retry_base_delay_seconds=1.0)

    # when the guard is computed
    # then it covers every attempt and the largest possible jitter, plus the grace period: three attempts
    # at 1 s, and a worst-case backoff of (1*2^0 + 1) then (1*2^1 + 1), so 5 s. Asserted as arithmetic
    # here so the integration test does not have to spend nine seconds proving it
    assert _hang_guard_seconds(task, settings) == pytest.approx(
        1.0 * 3 + (1.0 + 1.0 + 2.0 + 1.0) + _BACKSTOP_GRACE_SECONDS
    )


def test_a_finalise_step_that_raises_should_fail_without_a_retry() -> None:
    # given a task whose attempt succeeds and whose pure post-step raises
    attempts: list[int] = []

    def attempt(attempt_number: int) -> str:
        attempts.append(attempt_number)
        return "data"

    def finalise(_data: str, _attempt_number: int) -> str:
        raise RuntimeError("render broke")

    task: Task[str, str] = Task(name="Row", attempt=attempt, finalise=finalise, max_retries=2)
    context = make_report_context(settings=Settings(block_retries=2))

    # when the task runs
    results = execute_tasks(
        [task],
        settings=context.settings,
        clock=context.clock,
        logger=context.logger,
        run_id=context.run.run_id,
        vocabulary=BLOCK_EXECUTION,
    )

    # then it failed once: retrying a pure step is pointless, and the attempt was not repeated
    assert results[0].status is ExecutionStatus.FAILED
    assert results[0].error_type == "RuntimeError"
    assert attempts == [1]


def test_a_task_without_a_finaliser_should_return_its_attempt_value() -> None:
    # given a task that returns a value and has no post-step
    task: Task[tuple[str, ...], tuple[str, ...]] = Task(
        name="Source", attempt=lambda _attempt: ("a", "b")
    )
    context = make_report_context()

    # when it runs
    results = execute_tasks(
        [task],
        settings=context.settings,
        clock=context.clock,
        logger=context.logger,
        run_id=context.run.run_id,
        vocabulary=BLOCK_EXECUTION,
    )

    # then the value is the result, not None: the caller chose R = T
    assert results[0].status is ExecutionStatus.OK
    assert results[0].value == ("a", "b")


def test_execute_tasks_with_a_non_positive_override_should_raise_configuration_error() -> None:
    # given a task declaring an impossible budget
    task: Task[object, object] = Task(name="Bad", attempt=lambda _attempt: None, timeout_seconds=0)
    context = make_report_context()

    # when the run starts
    # then it is refused as a declaration error rather than producing an instant timeout
    with pytest.raises(ConfigurationError, match="timeout_seconds"):
        execute_tasks(
            [task],
            settings=context.settings,
            clock=context.clock,
            logger=context.logger,
            run_id=context.run.run_id,
            vocabulary=BLOCK_EXECUTION,
        )


def test_execute_tasks_with_no_tasks_should_raise_configuration_error() -> None:
    context = make_report_context()

    with pytest.raises(ConfigurationError, match="at least one task"):
        execute_tasks(
            [],
            settings=context.settings,
            clock=FakeClock(),
            logger=context.logger,
            run_id=context.run.run_id,
            vocabulary=BLOCK_EXECUTION,
        )

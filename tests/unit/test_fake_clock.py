"""The FakeClock contract.

These tests are short and look almost trivial, which is the point: the fake is what makes every timeout
and retry-backoff test in stage 5 trustworthy, so its three contracted properties are pinned here rather
than assumed wherever it is first used.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from auto_inform.application.ports import Clock
from tests.support.clocks import DEFAULT_START, FakeClock


def test_fake_clock_should_start_at_a_fixed_time_and_not_move_on_its_own() -> None:
    # given a fresh clock
    clock = FakeClock()

    # when time is read twice
    first_read, second_read = clock.now(), clock.now()

    # then neither reading has moved: a clock that advanced on its own would make every timing test in
    # stage 5 flaky for reasons nobody would think to look for
    assert first_read == second_read == DEFAULT_START
    assert clock.monotonic() == 0.0


def test_fake_clock_should_advance_now_and_monotonic_together() -> None:
    # given a clock at a known instant
    clock = FakeClock()

    # when it is advanced
    clock.advance(30)

    # then both readings moved by the same amount -- property 1 of the contract, and the reason a test
    # cannot end up exercising a clock the framework could never see
    assert clock.monotonic() == 30.0
    assert clock.now() == DEFAULT_START.replace(second=30)


def test_fake_clock_sleep_should_record_the_request_in_order() -> None:
    # given a clock and the backoff schedule a retry policy would ask for
    clock = FakeClock()

    # when sleeps are requested out of order and at different sizes
    for seconds in (1.0, 2.0, 4.0):
        clock.sleep(seconds)

    # then the schedule is available as data, in the order it was requested, which is what lets a test
    # assert the *shape* of the backoff rather than merely that it happened
    assert clock.sleeps == (1.0, 2.0, 4.0)


def test_fake_clock_sleep_should_also_move_time() -> None:
    # given a clock
    clock = FakeClock()

    # when a sleep is requested
    clock.sleep(5)

    # then time moved by that much, so code that measures elapsed time around a sleep sees it
    assert clock.monotonic() == 5.0
    assert clock.now() == DEFAULT_START.replace(second=5)


def test_fake_clock_sleep_should_not_actually_wait() -> None:
    # given a clock and a request that would take half a minute if it were honoured
    clock = FakeClock()
    started = time.monotonic()

    # when the sleep is requested
    clock.sleep(30)

    # then it returns immediately. The bound is deliberately loose -- 30 seconds of real waiting against
    # a one-second ceiling cannot be flaky -- and it is the opposite of asserting that something took at
    # least N seconds, which docs/09 §2 forbids because that is a delay, not a test
    assert time.monotonic() - started < 1.0


def test_fake_clock_advance_should_not_be_recorded_as_a_sleep() -> None:
    # given a clock
    clock = FakeClock()

    # when time is advanced directly, representing work that consumed time without asking to wait
    clock.advance(12)

    # then the sleep log stays empty, so a test asserting a backoff schedule is not confused by
    # simulated work
    assert clock.sleeps == ()
    assert clock.monotonic() == 12.0


@pytest.mark.parametrize("method", ["sleep", "advance"])
def test_fake_clock_should_refuse_to_move_backwards(method: str) -> None:
    # given a clock
    clock = FakeClock()

    # when a negative interval is requested
    # then it is refused rather than silently rewinding: a negative delay is a bug in the arithmetic
    # that produced it, and a clock that quietly accepted it would hide that
    with pytest.raises(ValueError, match="cannot move backwards"):
        getattr(clock, method)(-1)


def test_fake_clock_with_a_naive_start_should_raise_value_error() -> None:
    # given a start time with no timezone
    # when the clock is constructed
    # then it is refused: a naive clock would make every rendered timestamp wrong by the host's offset
    with pytest.raises(ValueError, match="timezone-aware"):
        FakeClock(start=datetime(2026, 10, 17, 9, 0))


def test_fake_clock_should_satisfy_the_clock_port() -> None:
    # given the port the framework injects into a context
    # when a fake is used where a Clock is expected
    port: Clock = FakeClock()

    # then mypy has verified the shape at type-check time, and the object is usable as one. Annotating
    # rather than reflecting means a missing or misnamed method fails the type check, not a stage-5 test
    # three weeks later
    assert port.monotonic() == 0.0
    port.sleep(1)
    assert port.now() > DEFAULT_START

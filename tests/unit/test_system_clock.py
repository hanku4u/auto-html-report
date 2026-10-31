"""The real clock, against its three contracts.

Two are cheap assertions; the third necessarily spends a few real milliseconds, because that is the one
thing this implementation exists to do.
"""

from __future__ import annotations

import time
from datetime import UTC

from auto_html_report.infrastructure.clock import SystemClock


def test_now_should_be_timezone_aware_and_in_utc() -> None:
    # given the system clock
    moment = SystemClock().now()

    # when the timestamp is inspected
    # then it carries UTC, so every rendered timestamp is comparable across hosts
    assert moment.tzinfo is not None
    assert moment.utcoffset() == UTC.utcoffset(moment)


def test_monotonic_should_not_go_backwards() -> None:
    # given two readings
    clock = SystemClock()
    first = clock.monotonic()
    second = clock.monotonic()

    # then the second is never earlier, which is what makes elapsed-time measurement safe across a clock
    # adjustment
    assert second >= first


def test_sleep_should_actually_wait() -> None:
    # given a short requested wait
    clock = SystemClock()
    started = time.monotonic()

    # when it sleeps
    clock.sleep(0.01)

    # then real time passed -- this is the one implementation where that is the point. The bound is loose
    # so it cannot be flaky; it only asserts that the call did not return instantly
    assert time.monotonic() - started >= 0.005

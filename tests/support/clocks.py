"""A clock whose time moves only when the code under test asks it to.

The contract, which ``docs/09-testing-and-acceptance.md`` §1 states and this module then enforces:

1. ``now()`` and ``monotonic()`` advance **together**, from one origin, so a test cannot drift the two
   apart -- a test that did so would be exercising a clock the framework can never see.
2. Every requested ``sleep()`` is recorded, in order, so a retry backoff schedule is asserted as data
   rather than costing real seconds.
3. ``sleep()`` never actually waits.

Implements the ``Clock`` port in ``src/auto_html_report/application/ports.py``. It lives in the test support
package rather than in the product because it is a double, and doubles are not shipped to consumers.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

#: A fixed, unremarkable wall-clock start, so a test that renders a timestamp is reproducible.
DEFAULT_START = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


class FakeClock:
    """A ``Clock`` that does not move unless asked.

    **One global timeline, not per-thread time.** Concurrent work advances this single clock: four blocks
    each advancing one second move it four seconds in total, so a test that measures elapsed time for
    concurrent blocks can see a duration inflated by its peers. Timing tests over concurrent work should
    therefore use generous budgets or zero simulated work. The methods are locked, so a lost update cannot
    happen, but the timeline itself is shared by design — it models a process, and a process has one clock.
    """

    def __init__(self, *, start: datetime = DEFAULT_START, monotonic_start: float = 0.0) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock.start must be timezone-aware")
        self._now = start
        self._monotonic = monotonic_start
        self._sleeps: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> datetime:
        """Wall-clock time, which advances only with :meth:`advance` or :meth:`sleep`."""
        with self._lock:
            return self._now

    def monotonic(self) -> float:
        """Elapsed time, which advances only with :meth:`advance` or :meth:`sleep`."""
        with self._lock:
            return self._monotonic

    def sleep(self, seconds: float) -> None:
        """Record a requested wait and move the clock instead of waiting for it."""
        self._check(seconds)
        with self._lock:
            self._sleeps.append(seconds)
            self._move(seconds)

    def advance(self, seconds: float) -> None:
        """Move time without recording a sleep.

        For the case where the *code under test* consumed time without asking to wait -- a slow API
        call the test is pretending happened.
        """
        self._check(seconds)
        with self._lock:
            self._move(seconds)

    @property
    def sleeps(self) -> tuple[float, ...]:
        """Every sleep requested so far, in the order it was requested."""
        with self._lock:
            return tuple(self._sleeps)

    def _move(self, seconds: float) -> None:
        # Caller holds the lock. Both readings, always, in the same call: property 1 of the contract.
        self._monotonic += seconds
        self._now += timedelta(seconds=seconds)

    @staticmethod
    def _check(seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"a clock cannot move backwards: got {seconds!r}")

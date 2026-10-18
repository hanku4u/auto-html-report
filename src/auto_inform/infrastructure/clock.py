"""The real ``Clock``: wall time, a monotonic counter, and an actual sleep.

The default implementation of the port in ``application/ports.py``. Tests use the fake in
``tests/support/clocks.py`` instead, so this one is exercised only for its own three contracts — one of
which necessarily takes real milliseconds.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    """The process's own clock, behind the framework's time seam."""

    def now(self) -> datetime:
        """Current wall-clock time, timezone-aware and in UTC."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin, for measuring elapsed time. Never goes backwards."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Actually block the calling thread for ``seconds``."""
        time.sleep(seconds)

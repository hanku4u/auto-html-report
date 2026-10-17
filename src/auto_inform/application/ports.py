"""Ports: the interfaces the application layer depends on, injected at the edge.

Only the two ports the domain layer's signatures require are declared at this stage. ``EventStore``
and ``OutputWriter`` arrive with the code that uses them -- stages 8 and 6 of
``docs/11-implementation-plan.md`` -- because declaring an interface with no implementation and no
caller is speculation, not design.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol


class Clock(Protocol):
    """The framework's single seam for time.

    Three methods rather than one because they answer different questions, and using one for
    another's job is how timing bugs get in:

    * ``now()`` is wall-clock time, for timestamps a human reads or that must be comparable across
      processes -- ``RunMetadata.started_at``, ``Report.generated_at``, ``Event.discovered_at``.
    * ``monotonic()`` is a non-decreasing value for measuring *elapsed* time -- durations, retry
      backoff, timeout budgets. Wall clock is wrong for this: an NTP step mid-run can produce a
      negative duration or a timeout that never fires.
    * ``sleep()`` is the only way the framework waits. Putting the wait behind the same seam is what
      makes the hermetic testing rule in ``docs/09`` §2 achievable: a fake clock records the delays
      it was asked for, so a backoff schedule is asserted as data rather than costing real seconds.

    The specification did not define this interface; it is recorded in ADR-0022. The default
    implementation, and the ``SystemClock`` that backs it, land with stage 7.
    """

    def now(self) -> datetime:
        """Current wall-clock time, timezone-aware and in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin, for measuring elapsed time. Never goes backwards."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for at least ``seconds``.

        This is the seam a fake replaces, not a general-purpose delay: nothing in the framework may
        wait any other way, or the backoff and timeout tests stop being hermetic.
        """
        ...


class HttpClient(Protocol):
    """Injected HTTP access.

    Implementations **must be thread-safe**: one instance is shared by every block in a report, and
    blocks run concurrently. ``timeout`` is a required keyword on every method, because the framework
    cannot interrupt a running thread -- see ``docs/02`` §6.2. Implementations map transport failures
    onto the framework's error types as described in ``docs/02`` §5.1.
    """

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Fetch ``path`` and parse the body as JSON.

        ``path`` may be absolute or relative to ``AUTO_INFORM_HTTP_BASE_URL``. Raises
        :class:`~auto_inform.domain.errors.TransientBlockError` for a failure worth retrying and
        :class:`~auto_inform.domain.errors.PermanentBlockError` for one that is not.
        """
        ...

    def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Fetch ``path`` and return the raw body. Error behaviour as above."""
        ...

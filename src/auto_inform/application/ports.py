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
    """Time, injected so that timeouts and retry backoff are testable.

    Two methods rather than one because they answer different questions: ``now()`` is wall-clock
    time for timestamps that a human reads, while ``monotonic()`` is a non-decreasing value for
    measuring elapsed time, which must not jump because the machine's clock was adjusted.

    Note: ``docs/07-configuration-and-logging.md`` does not specify this interface, so it is defined
    here by the requirements of the timeout accounting in ``docs/02`` §6.2.
    """

    def now(self) -> datetime:
        """Current wall-clock time, timezone-aware and in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin, for measuring elapsed time. Never goes backwards."""
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

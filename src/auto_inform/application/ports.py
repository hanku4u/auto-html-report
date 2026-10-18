"""Ports: the interfaces the application layer depends on, injected at the edge.

Each port lands with the code that uses it -- ``OutputWriter`` with the entry point in stage 6, and
``EventStore`` with discovery in stage 8 -- because declaring an interface with no implementation and no
caller is speculation, not design. ``docs/04-events-and-stores.md`` §2 is the normative description of
what a store must do; this module only states the shape.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Self

from auto_inform.domain.event import Event
from auto_inform.domain.report import OutputLocation


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


class OutputWriter(Protocol):
    """Where a finished report's bytes go.

    Implementations receive a complete filename and never derive one, so that a local writer and an S3
    writer cannot disagree about naming (``docs/06-output-html.md`` §2). A failed write raises
    :class:`~auto_inform.domain.errors.OutputError`, and the caller must not treat the report as
    delivered -- see the processing job's ordering rule in ``docs/05`` §2.
    """

    def write(self, *, filename: str, content: str) -> OutputLocation:
        """Persist ``content`` under ``filename`` and report where it landed."""
        ...


@dataclass(frozen=True, slots=True)
class SaveResult:
    """What one :meth:`EventStore.save` call did, in the two numbers a caller reports.

    ``save`` is insert-only: a row already present is a *duplicate* and is left completely untouched,
    including its status, attempts and payload. Re-discovery therefore reports a rolling window as
    duplicates rather than resetting work that has already been done (``docs/04`` §2.1).
    """

    new: int
    duplicates: int


class EventStore(Protocol):
    """Where discovered events live between the discovery and processing jobs.

    **Thread affinity is part of the contract:** every method must be called from the thread that
    constructed the store. The processing job marshals its store calls onto the main thread for exactly
    this reason (``docs/04`` §2.4, ``docs/05`` §4). SQLite is thread-affine by default, but the port
    forbids any implementation from assuming otherwise, so code that happens to work against a
    connection pool cannot quietly break the default backend.

    Stores are context managers, and ``close()`` is idempotent and safe after an exception, because a
    scheduled job must release its file handle or pool even when a report failed.
    """

    def save(self, events: Iterable[Event]) -> SaveResult:
        """Insert events, leaving existing rows untouched. Idempotent on ``(event_type, event_id)``."""
        ...

    def pending(
        self,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Events that still need a report: ``NEW``, under the attempt limit, oldest first.

        Never returns ``PROCESSED`` rows or retired ``FAILED`` ones, so a permanently broken event
        cannot be handed out for ever (ADR-0019).
        """
        ...

    def mark_processed(self, event: Event, *, output_uri: str) -> None:
        """Record that ``event`` produced a delivered document at ``output_uri``.

        Called only after the writer has returned successfully, so a failed upload leaves the event
        pending (``docs/05`` §3).
        """
        ...

    def mark_failed(self, event: Event, *, reason: str) -> None:
        """Record one failed attempt; retire the event when it reaches the attempt limit (ADR-0019)."""
        ...

    def close(self) -> None:
        """Release the underlying handle or pool. Idempotent."""
        ...

    def __enter__(self) -> Self:
        """Enter the context, returning the store itself."""
        ...

    def __exit__(self, *exc_info: object) -> None:
        """Close on the way out, whatever happened inside."""
        ...

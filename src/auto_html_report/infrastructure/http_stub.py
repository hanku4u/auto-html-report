"""The deterministic stand-in for the real API client.

Every external call is a stub in v1 (``docs/07-configuration-and-logging.md`` §2). Blocks and discoverers
are written against the ``HttpClient`` port, so replacing this with a real client later is a wiring change
and nothing else.

The stub enforces the two rules that would otherwise make a real client a bad retrofit:

1. **``timeout`` is a required keyword**, exactly as in the port — a block that forgets one would be
   caught here rather than in production.
2. **One instance is shared across threads**, so the fixture table is guarded by a lock and every method
   is safe to call concurrently.

An unknown path raises rather than returning an empty payload: a missing fixture must look like a missing
fixture, not like a service with no data.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from auto_html_report.domain.errors import ConfigurationError


class StubNotConfiguredError(ConfigurationError):
    """Raised when the stub has no fixture for a requested path.

    A ``ConfigurationError`` rather than a bare ``LookupError``, so the hierarchy's promise holds: a
    caller catching :class:`AutoHtmlReportError` catches a missing fixture too.
    """


class StubHttpClient:
    """Canned responses, keyed by path, with errors available on demand."""

    def __init__(self, fixtures: Mapping[str, Any] | None = None) -> None:
        self._fixtures: dict[str, Any] = dict(fixtures or {})
        self._errors: dict[str, BaseException] = {}
        self._lock = threading.Lock()

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Return the fixture for ``path``, whatever its type."""
        return self._respond(path)

    def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Return the fixture for ``path``, which must be bytes."""
        response = self._respond(path)
        if not isinstance(response, bytes):
            raise TypeError(f"the fixture for {path!r} is not bytes; use get_json for that path")
        return response

    def with_fixture(self, path: str, response: Any) -> StubHttpClient:
        """Record a response, returning self so a caller can chain."""
        with self._lock:
            self._fixtures[path] = response
        return self

    def raising_for(self, path: str, error: BaseException) -> StubHttpClient:
        """Make ``path`` raise, for exercising retry and error-cell behaviour without a server."""
        with self._lock:
            self._errors[path] = error
        return self

    def _respond(self, path: str) -> Any:
        with self._lock:
            error = self._errors.get(path)
            fixture = self._fixtures.get(path)
            known = path in self._fixtures

        if error is not None:
            raise error
        if not known:
            raise StubNotConfiguredError(
                f"no fixture for {path!r}; configure one with StubHttpClient(fixtures={{...}}) or "
                ".with_fixture(...). A loud failure beats a silently empty report."
            )
        return fixture

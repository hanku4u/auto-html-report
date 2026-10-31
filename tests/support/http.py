"""A canned-response HTTP client for tests.

The port's contract (``src/auto_html_report/application/ports.py``) requires ``timeout`` on every call and
says implementations must be thread-safe, because one instance is shared by every block in a report and
blocks run concurrently. This double honours both, so a test that passes here is not passing because the
double was easier to satisfy than the real thing.

It records the **timeout** as well as the path and parameters, because "every HTTP call carries a
timeout" is a rule a test should be able to check rather than a habit (`docs/02-block-authoring.md` §5).

The production stub -- ``StubHttpClient``, which demo reports run against -- lands with stage 7 and
lives in ``auto_html_report/infrastructure/http_stub.py``. This one exists so that stage 4's blocks can be
tested without waiting for it, and so that the framework's own tests never depend on a shipped fixture.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HttpCall:
    """One request the code under test made."""

    path: str
    params: Mapping[str, Any] = field(default_factory=dict)
    timeout: float = 0.0
    headers: Mapping[str, str] = field(default_factory=dict)


class FakeHttpClient:
    """Fixtures keyed by path, a record of what was asked for, and errors on demand."""

    def __init__(self, fixtures: Mapping[str, Any] | None = None) -> None:
        self._fixtures: dict[str, Any] = dict(fixtures or {})
        self._errors: dict[str, BaseException] = {}
        self._calls: list[HttpCall] = []
        # The port says implementations are shared across threads; a double that forgot would let a
        # concurrency bug through in the very tests written to catch one.
        self._lock = threading.Lock()

    # --- what the code under test calls ---------------------------------------

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        return self._respond(path, params, timeout, headers)

    def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        response = self._respond(path, params, timeout, headers)
        if not isinstance(response, bytes):
            raise TypeError(f"the fixture for {path!r} is not bytes; use get_json for that path")
        return response

    # --- arranging the answers ------------------------------------------------

    def with_fixture(self, path: str, response: Any) -> FakeHttpClient:
        """Record a response for ``path``. Returns self, so a test can chain."""
        self._fixtures[path] = response
        return self

    def raising_for(self, path: str, error: BaseException) -> FakeHttpClient:
        """Make ``path`` raise, for exercising error cells and retry behaviour."""
        self._errors[path] = error
        return self

    # --- what the test asserts on --------------------------------------------

    @property
    def calls(self) -> tuple[HttpCall, ...]:
        """Every call made, in order."""
        with self._lock:
            return tuple(self._calls)

    @property
    def paths(self) -> tuple[str, ...]:
        """Just the paths requested, in order."""
        return tuple(call.path for call in self.calls)

    def _respond(
        self,
        path: str,
        params: Mapping[str, Any] | None,
        timeout: float,
        headers: Mapping[str, str] | None,
    ) -> Any:
        with self._lock:
            self._calls.append(
                HttpCall(
                    path=path,
                    params=dict(params or {}),
                    timeout=timeout,
                    headers=dict(headers or {}),
                )
            )
            error = self._errors.get(path)

        if error is not None:
            raise error

        try:
            return self._fixtures[path]
        except KeyError:
            # Loud, deliberately: an unconfigured path that returned an empty payload would make a block
            # look like it had no data rather than like the test had no fixture.
            raise LookupError(
                f"no fixture for {path!r}; configure one with FakeHttpClient(fixtures={{...}}) "
                "or .with_fixture(...)"
            ) from None

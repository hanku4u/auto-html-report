"""A small HTTP client using only the standard library. Copy it, or use the client you already have.

What matters for retries is the exception: ``TransientError``, ``TimeoutError`` and ``ConnectionError`` are
retried by default, and anything else fails the report straight away.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from auto_html_report import TransientError

_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class UrllibHttpClient:
    """Holds no per-request state, so one instance is safe to share across threads."""

    def __init__(self, base_url: str, *, token: str | None = None) -> None:
        self._base_url = base_url
        self._auth = {"Authorization": f"Bearer {token}"} if token else {}

    def get_bytes(
        self, path: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> bytes:
        url = urllib.parse.urljoin(self._base_url, path)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=self._auth)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as error:
            kind = TransientError if error.code in _RETRYABLE_STATUSES else RuntimeError
            raise kind(f"{error.code} from {path}") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise RuntimeError(f"TLS failure for {path}") from error  # noqa: TRY004
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError(f"timed out fetching {path}") from error
            raise ConnectionError(f"could not reach {path}") from error

    def get_json(
        self, path: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> Any:
        body = self.get_bytes(path, params=params, timeout=timeout)
        try:
            return json.loads(body)
        except ValueError as error:
            raise ValueError(f"unparsable body from {path}") from error

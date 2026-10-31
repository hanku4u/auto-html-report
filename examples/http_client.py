"""A real ``HttpClient``, using only the standard library.

This is the adapter from ``docs/02-block-authoring.md`` §5.2, and a test keeps the two identical. Copy it into
a job script and pass it as ``http=`` to talk to a real API.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from auto_html_report import PermanentBlockError, TransientBlockError

_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class UrllibHttpClient:
    """Holds no per-request state, so one instance is safe to share across block threads."""

    def __init__(self, base_url: str, *, token: str | None = None) -> None:
        self._base_url = base_url
        self._auth = {"Authorization": f"Bearer {token}"} if token else {}

    def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        url = urllib.parse.urljoin(self._base_url, path)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={**self._auth, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as error:
            kind = TransientBlockError if error.code in _RETRYABLE_STATUSES else PermanentBlockError
            raise kind(f"{error.code} from {path}") from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise PermanentBlockError(f"TLS failure for {path}") from error
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError(f"timed out fetching {path}") from error
            raise ConnectionError(f"could not reach {path}") from error

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        body = self.get_bytes(path, params=params, timeout=timeout, headers=headers)
        try:
            return json.loads(body)
        except ValueError as error:
            raise PermanentBlockError(f"unparsable body from {path}") from error

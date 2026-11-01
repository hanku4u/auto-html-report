"""A throwaway HTTP API on localhost, for exercising the plumbing without a real service.

Requests travel through a real client and a real server, so authentication, retries, timeouts and error
mapping all run for real.

    with local_api() as api:
        client = UrllibHttpClient(api.url, token=api.token)
        client.get_json("/yield/failures", timeout=5.0)

It serves the data in ``examples.demo_api``, and these routes for testing failure handling:

    /test/flaky      503 for the first two requests, then 200
    /test/error      always 500
    /test/malformed  200 with a body that is not JSON
    /test/slow       never answers, so the caller's timeout fires
    anything else    404

Every route needs the bearer token, and answers 401 without it. To poke at it by hand, run
``uv run python -m examples.local_api`` and use the URL and token it prints.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from examples.demo_api import ROUTES

LOCAL_API_TOKEN = "local-test-token"

#: How many requests /test/flaky fails before recovering: within the default three retries.
FLAKY_FAILURES = 2

#: A backstop so a stuck request cannot outlive the process by long. Closing the server wakes it sooner.
_SLOW_ROUTE_SECONDS = 30.0


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, token: str) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.token = token
        self.hits: Counter[str] = Counter()
        self.lock = threading.Lock()
        self.stopping = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        with self.server.lock:
            self.server.hits[path] += 1
            hit = self.server.hits[path]

        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self._respond(401, {"error": "missing or wrong bearer token"})
        elif path == "/test/flaky":
            if hit <= FLAKY_FAILURES:
                self._respond(503, {"error": "try again"})
            else:
                self._respond(200, {"status": "recovered", "attempt": hit})
        elif path == "/test/error":
            self._respond(500, {"error": "always fails"})
        elif path == "/test/malformed":
            self._send(200, b"this is not JSON")
        elif path == "/test/slow":
            # Writes nothing even when woken: the caller has already given up.
            self.server.stopping.wait(_SLOW_ROUTE_SECONDS)
        elif path in ROUTES:
            self._respond(200, ROUTES[path])
        else:
            self._respond(404, {"error": f"no route for {path}"})

    def _respond(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet: a failure route being hit is the point, not a problem worth printing."""


@dataclass(frozen=True)
class LocalApi:
    """A running server: where it is, its token, and how many requests each path has received."""

    url: str
    token: str
    _server: _Server

    def hits(self, path: str) -> int:
        with self._server.lock:
            return self._server.hits[path]


@contextmanager
def local_api(*, port: int = 0, token: str = LOCAL_API_TOKEN) -> Iterator[LocalApi]:
    """Serve the test API for the duration of the block. The default port is whichever is free."""
    server = _Server(port, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, bound_port = server.server_address[:2]
        yield LocalApi(url=f"http://{host!s}:{bound_port}", token=token, _server=server)
    finally:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    with local_api(port=8765) as api:
        print(f"serving on {api.url}, token {api.token!r}; press Ctrl-C to stop", flush=True)
        print(
            f'try: curl -H "Authorization: Bearer {api.token}" {api.url}/yield/failures', flush=True
        )
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass

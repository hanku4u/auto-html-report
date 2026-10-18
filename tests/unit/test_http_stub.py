"""The HTTP stub: fixtures by path, loud failures, and one instance shared across threads."""

from __future__ import annotations

import threading

import pytest

from auto_inform.domain.errors import TransientBlockError
from auto_inform.infrastructure.http_stub import StubHttpClient, StubNotConfiguredError


def test_get_json_should_return_the_fixture_for_the_path() -> None:
    # given a canned response
    stub = StubHttpClient({"/maintenance/T-104": {"status": "ok", "records": []}})

    # when it is requested
    # then the payload comes back unchanged, so a block can be tested without a server
    assert stub.get_json("/maintenance/T-104", timeout=30.0) == {"status": "ok", "records": []}


def test_get_json_with_an_unknown_path_should_raise_rather_than_return_empty() -> None:
    # given no fixture for the path
    stub = StubHttpClient()

    # when it is requested
    # then the failure is loud. An empty payload would make a block look like it had no data rather than
    # like the test forgot a fixture
    with pytest.raises(StubNotConfiguredError, match="no fixture"):
        stub.get_json("/unknown", timeout=30.0)


def test_get_bytes_should_return_bytes_and_refuse_anything_else() -> None:
    # given one bytes fixture and one JSON fixture
    stub = StubHttpClient({"/chart.png": b"\x89PNG", "/data.json": {"a": 1}})

    # when each is fetched the right way
    assert stub.get_bytes("/chart.png", timeout=5.0) == b"\x89PNG"

    # then asking for bytes from a JSON fixture is a type error rather than a confusing later failure
    with pytest.raises(TypeError, match="not bytes"):
        stub.get_bytes("/data.json", timeout=5.0)


def test_raising_for_should_surface_the_configured_error() -> None:
    # given a path configured to fail transiently, as a 503 would
    stub = StubHttpClient({"/x": {"ok": True}}).raising_for(
        "/x", TransientBlockError("upstream said 503")
    )

    # when it is requested
    # then the framework error propagates, so retry and error-cell behaviour can be exercised offline
    with pytest.raises(TransientBlockError, match="503"):
        stub.get_json("/x", timeout=1.0)


def test_with_fixture_should_add_a_response_and_return_self() -> None:
    # given an empty stub
    stub = StubHttpClient()

    # when a fixture is added in the fluent style
    added = stub.with_fixture("/later", {"value": 1})

    # then it is the same object, so a test can build a stub in one expression
    assert added is stub
    assert stub.get_json("/later", timeout=1.0) == {"value": 1}


def test_get_json_should_require_a_timeout_keyword() -> None:
    # given the port's rule that every call carries a timeout
    stub = StubHttpClient({"/x": {}})

    # when a caller omits it
    # then the signature refuses -- a block that forgets one fails here rather than in production where
    # the framework cannot interrupt a hung thread
    with pytest.raises(TypeError):
        stub.get_json("/x")  # type: ignore[call-arg]


def test_stub_should_be_safe_from_many_threads() -> None:
    # given one stub shared by every block in a report, as the port requires
    stub = StubHttpClient({"/x": {"value": 1}})
    results: list[object] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def fetch() -> None:
        start.wait()
        try:
            results.append(stub.get_json("/x", timeout=1.0))
        except BaseException as exc:  # noqa: BLE001 - surface anything at all
            errors.append(exc)

    # when eight threads request it at once
    threads = [threading.Thread(target=fetch) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    # then every call succeeded with the same answer
    assert errors == []
    assert results == [{"value": 1}] * 8

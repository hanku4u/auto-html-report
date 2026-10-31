"""The S3 writer, against an injected stub client -- no network, no credentials.

``docs/06-output-html.md`` §1 is the contract: the writer receives a complete filename, uploads the UTF-8
bytes with the report's content type, and raises ``OutputError`` when the upload fails so the processing job
leaves the event undelivered. The transport-timeout test is the one that keeps a stalled upload from
wedging the job (ADR-0030).
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from auto_html_report.domain.errors import ConfigurationError, OutputError
from auto_html_report.infrastructure.writers.s3_writer import S3Writer


class _StubClient:
    """Records every ``put_object`` call; can be told to fail, and is safe across threads."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._failure = failure
        self._lock = threading.Lock()

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.calls.append(kwargs)
        if self._failure is not None:
            raise self._failure
        return {}


def test_write_should_upload_the_filename_as_the_key_and_report_the_uri() -> None:
    # given a writer for a bucket
    client = _StubClient()
    writer = S3Writer("reports-bucket", client=client)

    # when a document is written
    location = writer.write(filename="report__x__20261018T120000Z.html", content="<html>ok</html>")

    # then the key is the filename untouched, the body is UTF-8, and the URI names both
    (call,) = client.calls
    assert call["Bucket"] == "reports-bucket"
    assert call["Key"] == "report__x__20261018T120000Z.html"
    assert call["Body"] == b"<html>ok</html>"
    assert call["ContentType"] == "text/html; charset=utf-8"
    assert location.kind == "s3"
    assert location.uri == "s3://reports-bucket/report__x__20261018T120000Z.html"


def test_a_prefix_should_be_joined_with_one_slash() -> None:
    # given a prefix written with stray slashes
    client = _StubClient()
    writer = S3Writer("bucket", "/team/reports/", client=client)

    # when a document is written
    location = writer.write(filename="r.html", content="x")

    # then the key and URI have exactly one separator and no leading slash
    assert client.calls[0]["Key"] == "team/reports/r.html"
    assert location.uri == "s3://bucket/team/reports/r.html"


def test_server_side_encryption_should_be_requested_only_when_configured() -> None:
    # given one writer with encryption and one without
    encrypted, plain = _StubClient(), _StubClient()

    # when each writes
    S3Writer("bucket", client=encrypted, server_side_encryption="AES256").write(
        filename="r.html", content="x"
    )
    S3Writer("bucket", client=plain).write(filename="r.html", content="x")

    # then only the configured one asks for it
    assert encrypted.calls[0]["ServerSideEncryption"] == "AES256"
    assert "ServerSideEncryption" not in plain.calls[0]


def test_a_failed_upload_should_raise_output_error_with_the_cause() -> None:
    # given a client whose upload fails
    writer = S3Writer("bucket", client=_StubClient(failure=RuntimeError("access denied")))

    # when a document is written
    # then it is an OutputError naming the key, with the cause chained for the log -- the caller must not
    # mark the event delivered
    with pytest.raises(OutputError, match="access denied") as raised:
        writer.write(filename="r.html", content="x")
    assert isinstance(raised.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    "filename",
    ["../escape.html", "sub/dir/report.html", "", ".", "..\\evil.html", "a\nb.html"],
)
def test_a_filename_that_is_a_path_should_be_refused_before_uploading(filename: str) -> None:
    # given a name that is a path rather than a bare filename
    client = _StubClient()
    writer = S3Writer("bucket", client=client)

    # when it is written
    # then it is refused and nothing was uploaded: writers never derive or escape a path
    with pytest.raises(OutputError, match="plain filename"):
        writer.write(filename=filename, content="x")
    assert client.calls == []


def test_concurrent_writes_should_share_one_client_safely() -> None:
    # given one writer, as the processing job shares it across report workers
    client = _StubClient()
    writer = S3Writer("bucket", client=client)
    errors: list[BaseException] = []

    def upload(index: int) -> None:
        try:
            writer.write(filename=f"r{index}.html", content="x")
        except BaseException as exc:  # noqa: BLE001 - the point is to capture anything raised
            errors.append(exc)

    # when eight threads write at once
    threads = [threading.Thread(target=upload, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # then every upload happened and nothing raised
    assert errors == []
    assert sorted(call["Key"] for call in client.calls) == [f"r{i}.html" for i in range(8)]


def test_the_client_should_be_created_with_bounded_transport_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given boto3 is available and captured at client creation
    boto3 = pytest.importorskip("boto3")
    captured: dict[str, Any] = {}

    class _Created:
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            return {}

    def fake_client(service: str, config: Any = None) -> _Created:
        captured["service"] = service
        captured["config"] = config
        return _Created()

    monkeypatch.setattr(boto3, "client", fake_client)

    # when a writer is constructed without an injected client
    S3Writer("bucket", timeout_seconds=7.5)

    # then the S3 client has bounded connect and read timeouts: the runner waits for write() with no
    # per-report deadline, so an unbounded client could wedge the job (ADR-0030)
    assert captured["service"] == "s3"
    config = captured["config"]
    assert (config.connect_timeout, config.read_timeout) == (7.5, 7.5)


def test_an_empty_bucket_should_be_refused() -> None:
    # given no bucket, which the loader would have caught but Settings can be constructed directly
    with pytest.raises(ConfigurationError, match="OUTPUT_S3_BUCKET"):
        S3Writer("", client=_StubClient())


def test_a_non_positive_timeout_should_be_refused() -> None:
    # given a timeout that would fail every upload immediately
    with pytest.raises(ConfigurationError, match="finite number"):
        S3Writer("bucket", client=_StubClient(), timeout_seconds=0)


@pytest.mark.parametrize(
    "bucket",
    ["Bad_Bucket", "ab", "192.168.1.1", "a..b", "-leading", "trailing-"],
    ids=["uppercase", "too-short", "ip-like", "double-dot", "leading-hyphen", "trailing-hyphen"],
)
def test_an_invalid_bucket_name_should_be_refused_at_construction(bucket: str) -> None:
    # given a bucket name the service would reject, and an injected client so no network is touched
    # when the writer is constructed
    # then it fails now rather than at the first report (ADR-0031)
    with pytest.raises(ConfigurationError, match="bucket"):
        S3Writer(bucket, client=_StubClient())


@pytest.mark.parametrize("mode", ["bogus", "aes256", "aws:kms:dsse:extra"])
def test_an_unsupported_encryption_mode_should_be_refused_at_construction(mode: str) -> None:
    # given an encryption mode the API does not accept
    # when the writer is constructed
    # then it fails at start-up, not on the first upload
    with pytest.raises(ConfigurationError, match="encryption"):
        S3Writer("bucket", client=_StubClient(), server_side_encryption=mode)

"""The optional S3 writer: the same document, uploaded instead of written.

``docs/06-output-html.md`` §1 is normative: the writer receives a complete filename, never invents one, and
raises ``OutputError`` when the upload fails so the processing job does not mark the event delivered.

**boto3 is imported when a writer is constructed**, not when this module is imported, so ``import
auto_inform`` works without the extra. Constructing one without it raises ``ConfigurationError`` naming the
extra. An injected ``client`` skips the import entirely, which is how the tests exercise this without a
network or credentials.

**Transport timeouts are mandatory, not tuning.** The processing runner waits for ``write()`` with no
per-report deadline (ADR-0030); only blocks have budgets and a hang guard. An S3 client without timeouts
turns a stalled network into a wedged job instead of an ``OutputError``, so the client is created here with
bounded connect and read timeouts.
"""

from __future__ import annotations

import math
import re
from typing import Any

from auto_inform.domain.errors import ConfigurationError, OutputError
from auto_inform.domain.report import OutputLocation
from auto_inform.infrastructure.writers._names import check_filename

#: HTML in UTF-8, which is what the rendered document always is.
DEFAULT_CONTENT_TYPE = "text/html; charset=utf-8"

#: Connect and read timeout for the S3 client, in seconds. One value for both: a report is small and an
#: upload that cannot connect or cannot finish in this window is not going to succeed by waiting longer.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: S3's naming rule: 3-63 characters, lowercase letters, digits, dots and hyphens, starting and ending
#: alphanumeric, no consecutive dots. A typo here fails on the first report otherwise, after work was done.
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

#: The encryption modes the API accepts. An unknown string would be rejected at the first upload.
_ALLOWED_ENCRYPTION = frozenset({"AES256", "aws:kms", "aws:kms:dsse"})

#: A timeout longer than this is not a bound worth having; it is also a sign of a units mistake.
_MAX_TIMEOUT_SECONDS = 300.0

#: boto3's own retry count. The framework does not retry a write; the client does, for transient transport
#: errors that never reached a decision.
_CLIENT_RETRIES = 3


def _connect(timeout_seconds: float) -> Any:
    """Create a boto3 S3 client with bounded transport timeouts."""
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigurationError(
            "AUTO_INFORM_OUTPUT_KIND=s3 requires the 's3' extra: uv add 'auto_inform[s3]'"
        ) from exc

    return boto3.client(
        "s3",
        config=Config(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"max_attempts": _CLIENT_RETRIES},
        ),
    )


class S3Writer:
    """Uploads each report to ``bucket`` under ``prefix``, with the document's own filename as the key."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        client: Any | None = None,
        server_side_encryption: str | None = None,
        content_type: str = DEFAULT_CONTENT_TYPE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not bucket:
            raise ConfigurationError(
                "AUTO_INFORM_OUTPUT_S3_BUCKET is required when AUTO_INFORM_OUTPUT_KIND=s3"
            )
        if not _BUCKET_NAME.match(bucket) or ".." in bucket:
            # Validated here, not by the service: a typo'd bucket otherwise fails at the first report,
            # after other workers may already have uploaded (ADR-0031).
            raise ConfigurationError(f"S3 bucket {bucket!r} is not a valid bucket name")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS:
            # `<= 0` would accept NaN and infinity, and both disable the timeout ADR-0030 requires.
            raise ConfigurationError(
                f"timeout_seconds is {timeout_seconds!r}; it must be a finite number between 0 and "
                f"{_MAX_TIMEOUT_SECONDS:g}"
            )
        if server_side_encryption is not None and server_side_encryption not in _ALLOWED_ENCRYPTION:
            raise ConfigurationError(
                f"unsupported server-side encryption {server_side_encryption!r}; expected one of "
                f"{', '.join(sorted(_ALLOWED_ENCRYPTION))}"
            )

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._server_side_encryption = server_side_encryption
        self._content_type = content_type
        self._client = client if client is not None else _connect(timeout_seconds)

    def write(self, *, filename: str, content: str) -> OutputLocation:
        """Upload ``content`` as ``filename``, and say where it landed."""
        check_filename(filename)
        key = f"{self._prefix}/{filename}" if self._prefix else filename

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content.encode("utf-8"),
                ContentType=self._content_type,
                **self._encryption(),
            )
        except Exception as exc:
            # Any client failure is an output failure: the event must stay undelivered, and the cause is
            # chained for the log (docs/06 §1).
            raise OutputError(
                f"could not upload {key!r} to {self._bucket!r}: {type(exc).__name__}: {exc}"
            ) from exc

        return OutputLocation(kind="s3", uri=f"s3://{self._bucket}/{key}")

    def _encryption(self) -> dict[str, str]:
        if self._server_side_encryption is None:
            return {}
        return {"ServerSideEncryption": self._server_side_encryption}

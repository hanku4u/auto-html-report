"""Settings: the frozen, validated view of the process environment.

``load_settings`` is the only place in this package permitted to read the environment. It parses every
``AUTO_INFORM_*`` variable once, at the edge, and hands the rest of the framework a frozen object — which
is what makes a test able to construct settings directly and never touch ``os.environ``.

Two rules in here are deliberate and both come from ``docs/07-configuration-and-logging.md`` §1.1:

* **Every invalid variable is reported in one exception.** A CI job that has to run four times to reveal
  four typos is a bad job.
* **Secrets are redacted from ``repr``.** The first debugging session in production will log the settings
  object, and that must be boring.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from auto_inform.domain.errors import ConfigurationError

OutputKind = Literal["local", "s3"]
StoreKind = Literal["sqlite", "postgres"]
LogFormat = Literal["json", "console"]
ErrorDetail = Literal["summary", "detailed"]

_REDACTED: Final = "***"
_SECRET_HINTS: Final = ("TOKEN", "SECRET", "PASSWORD", "DSN", "KEY")
_TRUE: Final = frozenset({"1", "true", "yes", "on"})
_FALSE: Final = frozenset({"0", "false", "no", "off"})
_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration for one job run. Construct directly in tests; never read the environment."""

    # --- output ---------------------------------------------------------------
    output_kind: OutputKind = "local"
    output_dir: Path = Path("./reports")
    output_s3_bucket: str | None = None
    output_s3_prefix: str = ""
    output_s3_sse: str | None = None

    # --- event store ----------------------------------------------------------
    event_store: StoreKind = "sqlite"
    #: ``None`` means "resolve to <output_dir>/events.sqlite3", which cannot be a plain default
    #: because it is derived from another field. The store factory resolves it (stage 8).
    sqlite_path: Path | None = None
    postgres_dsn: str | None = None
    event_max_attempts: int = 3

    # --- execution ------------------------------------------------------------
    max_workers: int = 8
    max_concurrent_reports: int = 1
    block_timeout_seconds: float = 60.0
    block_retries: int = 2
    retry_base_delay_seconds: float = 1.0
    retryable_exceptions: tuple[str, ...] = ()

    # --- observability --------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    error_detail: ErrorDetail = "summary"
    fail_on_block_error: bool = True

    # --- http -----------------------------------------------------------------
    http_base_url: str | None = None
    http_api_token: str | None = None
    http_max_redirects: int = 5

    def __repr__(self) -> str:
        """Every field, with anything secret-shaped redacted.

        The first debugging session in production will log this object; a DSN or token must not be in
        that output. The check is on the *attribute name*, so a new secret field is covered as soon as it
        is named like one.
        """
        parts: list[str] = []
        for name in self.__slots__:
            value = getattr(self, name)
            if value is not None and any(hint in name.upper() for hint in _SECRET_HINTS):
                value = _REDACTED
            parts.append(f"{name}={value!r}")
        return f"Settings({', '.join(parts)})"


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Parse and validate the environment into a frozen :class:`Settings`.

    ``environ`` is injectable so tests never touch ``os.environ``. Collects every problem before raising,
    and validates the cross-field requirements at load time rather than at first use.
    """
    source = os.environ if environ is None else environ
    errors: list[str] = []

    def raw(name: str, default: str | None) -> str | None:
        return source.get(name, default)

    def optional(name: str) -> str | None:
        value = source.get(name)
        return value if value else None

    def number(name: str, default: float, *, minimum: float) -> float:
        value = source.get(name)
        if value is None:
            return default
        try:
            parsed = float(value)
        except ValueError:
            errors.append(f"{name}={value!r} is not a number")
            return default
        if not math.isfinite(parsed):
            # NaN and infinity pass every `<` comparison, and both are actively dangerous: a NaN budget
            # makes `consumed > budget` always false, disabling the per-attempt timeout entirely, and an
            # infinite budget makes the hang guard unreachable. `time.sleep(nan)` also raises a raw
            # ValueError on the first retry.
            errors.append(f"{name}={value!r} must be a finite number")
            return default
        if parsed < minimum:
            errors.append(f"{name}={value!r} must be at least {minimum:g}")
            return default
        return parsed

    def integer(name: str, default: int, *, minimum: int) -> int:
        value = source.get(name)
        if value is None:
            return default
        try:
            parsed = int(value)
        except ValueError:
            errors.append(f"{name}={value!r} is not an integer")
            return default
        if parsed < minimum:
            errors.append(f"{name}={value!r} must be at least {minimum}")
            return default
        return parsed

    def boolean(name: str, default: bool) -> bool:
        value = source.get(name)
        if value is None:
            return default
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        errors.append(f"{name}={value!r} is not a boolean (use true or false)")
        return default

    def choice(name: str, default: str, options: tuple[str, ...]) -> str:
        value = source.get(name)
        if value is None:
            return default
        # Case-insensitive, and the canonical (lower-case) form is what gets stored. `boolean` already
        # accepted TRUE, so rejecting JSON purely on its case was an inconsistency rather than a policy.
        normalised = value.strip().lower()
        if normalised not in options:
            errors.append(f"{name}={value!r} is not one of {', '.join(options)}")
            return default
        return normalised

    def log_level(name: str, default: str) -> str:
        value = source.get(name)
        if value is None:
            return default
        normalised = value.strip().upper()
        if normalised not in _LEVELS:
            # Validated here rather than left to `logging.setLevel`, which raises a raw ValueError from
            # a different part of the program -- and which never appears in this loader's collected
            # error report, breaking the "report every invalid variable" rule.
            errors.append(f"{name}={value!r} is not one of {', '.join(sorted(_LEVELS))}")
            return default
        return normalised

    def name_list(name: str) -> tuple[str, ...]:
        value = source.get(name)
        if not value:
            return ()
        return tuple(part.strip() for part in value.split(",") if part.strip())

    sqlite_path = optional("AUTO_INFORM_SQLITE_PATH")

    settings = Settings(
        output_kind=choice("AUTO_INFORM_OUTPUT_KIND", "local", ("local", "s3")),  # type: ignore[arg-type]
        output_dir=Path(raw("AUTO_INFORM_OUTPUT_DIR", "./reports") or "./reports"),
        output_s3_bucket=optional("AUTO_INFORM_OUTPUT_S3_BUCKET"),
        output_s3_prefix=raw("AUTO_INFORM_OUTPUT_S3_PREFIX", "") or "",
        output_s3_sse=optional("AUTO_INFORM_OUTPUT_S3_SSE"),
        event_store=choice("AUTO_INFORM_EVENT_STORE", "sqlite", ("sqlite", "postgres")),  # type: ignore[arg-type]
        sqlite_path=Path(sqlite_path) if sqlite_path else None,
        postgres_dsn=optional("AUTO_INFORM_POSTGRES_DSN"),
        event_max_attempts=integer("AUTO_INFORM_EVENT_MAX_ATTEMPTS", 3, minimum=1),
        max_workers=integer("AUTO_INFORM_MAX_WORKERS", 8, minimum=1),
        max_concurrent_reports=integer("AUTO_INFORM_MAX_CONCURRENT_REPORTS", 1, minimum=1),
        block_timeout_seconds=number("AUTO_INFORM_BLOCK_TIMEOUT_SECONDS", 60.0, minimum=0.001),
        block_retries=integer("AUTO_INFORM_BLOCK_RETRIES", 2, minimum=0),
        retry_base_delay_seconds=number("AUTO_INFORM_RETRY_BASE_DELAY_SECONDS", 1.0, minimum=0.0),
        retryable_exceptions=name_list("AUTO_INFORM_RETRYABLE_EXCEPTIONS"),
        log_level=log_level("AUTO_INFORM_LOG_LEVEL", "INFO"),
        log_format=choice("AUTO_INFORM_LOG_FORMAT", "json", ("json", "console")),  # type: ignore[arg-type]
        error_detail=choice("AUTO_INFORM_ERROR_DETAIL", "summary", ("summary", "detailed")),  # type: ignore[arg-type]
        fail_on_block_error=boolean("AUTO_INFORM_FAIL_ON_BLOCK_ERROR", True),
        http_base_url=optional("AUTO_INFORM_HTTP_BASE_URL"),
        http_api_token=optional("AUTO_INFORM_HTTP_API_TOKEN"),
        http_max_redirects=integer("AUTO_INFORM_HTTP_MAX_REDIRECTS", 5, minimum=0),
    )

    # Cross-field requirements, checked once the individual values parsed or fell back to their default.
    if settings.output_kind == "s3" and not settings.output_s3_bucket:
        errors.append("AUTO_INFORM_OUTPUT_S3_BUCKET is required when AUTO_INFORM_OUTPUT_KIND=s3")
    if settings.event_store == "postgres" and not settings.postgres_dsn:
        errors.append("AUTO_INFORM_POSTGRES_DSN is required when AUTO_INFORM_EVENT_STORE=postgres")

    if errors:
        raise ConfigurationError(
            "invalid configuration:\n" + "\n".join(f"  - {problem}" for problem in errors)
        )

    return settings

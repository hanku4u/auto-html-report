"""Settings: the frozen, validated view of the process environment.

**Stage 2 defines the shape only.** This module exists now because the domain layer's contexts are
typed in terms of it, and a signature naming a type that does not exist cannot be type-checked.
The behaviour lands with its own stage:

* ``load_settings()`` -- parsing and validating ``AUTO_INFORM_*`` -- is stage 7, and is the *only*
  thing in the package permitted to read the environment. See
  ``docs/07-configuration-and-logging.md`` §1.1 for the environment variable to attribute map, which
  is part of the contract because blocks read these attributes.
* Secret redaction in ``__repr__`` is also stage 7, where the policy is specified. The defaults
  below contain no secrets, so the interim state leaks nothing.

Field defaults mirror the documented defaults exactly. Stage 7 adds the test that holds the table in
``docs/07`` §1.1 and this class together, since until the environment parsing exists there is no
mapping to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration for one job run. Construct directly in tests; never read the environment."""

    # --- output ---------------------------------------------------------------
    output_kind: Literal["local", "s3"] = "local"
    output_dir: Path = Path("./reports")
    output_s3_bucket: str | None = None
    output_s3_prefix: str = ""
    output_s3_sse: str | None = None

    # --- event store ----------------------------------------------------------
    event_store: Literal["sqlite", "postgres"] = "sqlite"
    #: ``None`` means "resolve to <output_dir>/events.sqlite3", which cannot be a plain default
    #: because it is derived from another field.
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
    log_format: Literal["json", "console"] = "json"
    error_detail: Literal["summary", "detailed"] = "summary"
    fail_on_block_error: bool = True

    # --- http -----------------------------------------------------------------
    http_base_url: str | None = None
    http_api_token: str | None = None
    http_max_redirects: int = 5

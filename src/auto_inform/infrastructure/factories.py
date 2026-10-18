"""The context factories: the seam between the environment and injected dependencies.

These build the four context objects and the output writer from a frozen ``Settings``, and they live in
``infrastructure/`` because they are the one place that may see every concrete adapter — the system clock,
the stub client, the writers. Nothing above this layer names an adapter.

Each returns a value rather than registering a global, so a long-lived process can build several contexts
with different settings without them contaminating each other, and each run gets a fresh ``run_id``, which
is what makes a report traceable back to the log lines that produced it.

The keyword arguments that override a dependency are an addition to the signatures in
``docs/07`` §3: they default to the production choice, so the documented call still works, and they make
these factories testable and usable by a demo job that wants canned fixtures.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from auto_inform.application.context import DiscoveryContext, ReportContext, RunMetadata
from auto_inform.domain.errors import ConfigurationError
from auto_inform.infrastructure.clock import SystemClock
from auto_inform.infrastructure.http_stub import StubHttpClient
from auto_inform.infrastructure.writers.local_writer import LocalDirectoryWriter

if TYPE_CHECKING:
    from auto_inform.application.ports import Clock, HttpClient, OutputWriter
    from auto_inform.infrastructure.settings import Settings

_LOGGER_NAME = "auto_inform"


def new_report_context(
    settings: Settings,
    *,
    http: HttpClient | None = None,
    clock: Clock | None = None,
    logger: logging.Logger | None = None,
) -> ReportContext:
    """Generate a fresh run id and start time, and wire the dependencies from ``settings``.

    The clock is resolved first and used for ``started_at`` as well as for the context: injecting a clock
    and still reading wall time for the run metadata would make the override a lie, and ADR-0022 makes
    this the framework's single seam for time.
    """
    resolved_clock = clock if clock is not None else SystemClock()
    return ReportContext(
        settings=settings,
        http=http if http is not None else StubHttpClient(),
        logger=logger if logger is not None else logging.getLogger(_LOGGER_NAME),
        clock=resolved_clock,
        run=_new_run(resolved_clock),
    )


def new_discovery_context(
    settings: Settings,
    *,
    http: HttpClient | None = None,
    clock: Clock | None = None,
    logger: logging.Logger | None = None,
) -> DiscoveryContext:
    """As above, for the discovery phase. A separate context means a separate ``run_id``."""
    resolved_clock = clock if clock is not None else SystemClock()
    return DiscoveryContext(
        settings=settings,
        http=http if http is not None else StubHttpClient(),
        logger=logger if logger is not None else logging.getLogger(_LOGGER_NAME),
        clock=resolved_clock,
        run=_new_run(resolved_clock),
    )


def build_output_writer(settings: Settings) -> OutputWriter:
    """The writer selected by ``AUTO_INFORM_OUTPUT_KIND``.

    Only ``local`` is implemented. ``s3`` raises rather than silently falling back to the filesystem: a
    job that believes it is uploading and is not is worse than a job that fails at start-up. The S3 branch
    arrives with stage 11.
    """
    if settings.output_kind == "local":
        return LocalDirectoryWriter(settings.output_dir)

    raise ConfigurationError(
        "AUTO_INFORM_OUTPUT_KIND=s3 is not implemented yet; the S3 writer arrives with stage 11. "
        "Use AUTO_INFORM_OUTPUT_KIND=local for now."
    )


def _new_run(clock: Clock) -> RunMetadata:
    """A fresh run identity, stamped by the injected clock rather than by wall time."""
    return RunMetadata(run_id=uuid.uuid4().hex, started_at=clock.now())

"""Builders for the injected contexts.

A block's ``collect()`` receives a ``ReportContext``; these build one with everything filled in, so a
test only has to say which part it cares about. The clock is a ``FakeClock`` rather than the real one,
which is what keeps a test that exercises a timeout from waiting for it.
"""

from __future__ import annotations

import logging
from datetime import datetime

from auto_inform.application.context import (
    DiscoveryContext,
    RenderContext,
    ReportContext,
    RunMetadata,
)
from auto_inform.infrastructure.settings import Settings
from tests.support.builders import FIXED_RUN_ID
from tests.support.clocks import DEFAULT_START, FakeClock
from tests.support.http import FakeHttpClient

TITLE = "Test report"


def make_report_context(
    *,
    http: FakeHttpClient | None = None,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
) -> ReportContext:
    """A context whose HTTP client is a fake and whose clock does not move."""
    return ReportContext(
        settings=settings or Settings(),
        http=http or FakeHttpClient(),
        logger=logging.getLogger("auto_inform.tests"),
        clock=clock or FakeClock(),
        run=RunMetadata(run_id=FIXED_RUN_ID, started_at=DEFAULT_START),
    )


def make_render_context(
    *,
    report_title: str = TITLE,
    generated_at: datetime = DEFAULT_START,
) -> RenderContext:
    """The render context, which carries no clock, no HTTP client and no logger by design."""
    return RenderContext(generated_at=generated_at, report_title=report_title)


def make_discovery_context(
    *,
    http: FakeHttpClient | None = None,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
) -> DiscoveryContext:
    """A discovery context, wired like a report context with its own run identity.

    Same shape as :class:`ReportContext` by design (``docs/05`` §1), so it gets the same builder
    treatment: the fake clock is what lets a timeout test finish instantly.
    """
    return DiscoveryContext(
        settings=settings or Settings(),
        http=http or FakeHttpClient(),
        logger=logging.getLogger("auto_inform.tests"),
        clock=clock or FakeClock(),
        run=RunMetadata(run_id=FIXED_RUN_ID, started_at=DEFAULT_START),
    )

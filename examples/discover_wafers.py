"""Discovery job: find failed wafers and persist them as events.

The first half of the two-job pipeline (``docs/05`` §1). It is the shape of ``docs/05`` §6 with the v1
stub client wired to demo fixtures, so it runs end to end **as a module**, from the repository root:

    uv run python -m examples.discover_wafers
    uv run python -m examples.process_events

``-m`` rather than the path: running the file directly puts ``examples/`` on ``sys.path``, not the
repository root, and the shared fixtures in ``examples.demo_api`` would not import.

A scheduled deployment replaces ``demo_http()`` with the real client; nothing else changes. ``main()``
takes an optional ``Settings`` so the test suite can run it without touching the environment; a job script
calls it with none.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

from auto_html_report import (
    ConfigurationError,
    Discoverer,
    DiscoveryContext,
    Event,
    ExitCode,
    Settings,
    StoreError,
    configure_logging,
    exit_code_for,
    load_settings,
    new_discovery_context,
    run_discovery,
)
from examples.demo_api import TOOL_ID, demo_http
from examples.store import open_store


class WaferFailureDiscoverer(Discoverer):
    """Finds the lots that failed yield, one event per lot."""

    event_type = "WAFER_FAILURE"

    def discover(self, context: DiscoveryContext) -> Iterable[Event]:
        payload = context.http.get_json("/yield/failures", timeout=30.0)
        for lot_id in payload["lots"]:
            yield Event(
                event_type=self.event_type,
                event_id=f"{self.event_type}:{lot_id}",
                payload={
                    "lot_id": lot_id,
                    "tool_id": TOOL_ID,
                    "summary": "Below yield threshold",
                },
                discovered_at=context.clock.now(),
                source="yield-api",
            )


def main(settings: Settings | None = None) -> int:
    try:
        resolved = load_settings() if settings is None else settings
        configure_logging(resolved.log_level, resolved.log_format)
        with open_store(resolved) as store:
            run = run_discovery(
                [WaferFailureDiscoverer()],
                store=store,
                context=new_discovery_context(resolved, http=demo_http()),
            )
    except ConfigurationError:
        return ExitCode.CONFIGURATION_ERROR
    except StoreError:
        return ExitCode.STORE_ERROR
    return exit_code_for(run)


if __name__ == "__main__":
    sys.exit(main())

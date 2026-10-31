"""The manufacturing report family: one report per failed wafer.

``docs/03-report-api.md`` §3 is the convention this follows: the factory receives the whole :class:`Event`
and binds its fields into each block's **constructor**. The event is never tucked into ``ReportContext``,
so a block that needs ``tool_id`` declares it, and cannot silently depend on ambient state a caller forgot
to populate.

Importing this module registers the factory. That is deliberate -- a job script imports the families it
deploys, and forgetting one is loud rather than silent: every event of that type is recorded as a
``ConfigurationError`` and the run exits ``2`` (``docs/05`` §6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_html_report.blocks.maintenance_history import MaintenanceHistoryBlock
from auto_html_report.blocks.wafer_map import WaferMapBlock
from auto_html_report.domain.event import Event
from auto_html_report.domain.report import ReportDefinition
from auto_html_report.registry import register_report

if TYPE_CHECKING:
    from auto_html_report.application.context import ReportContext

#: The event type this family answers. Spelled once, so a job script imports the name instead of retyping it.
WAFER_FAILURE_EVENT = "WAFER_FAILURE"


def _payload_text(event: Event, field: str) -> str:
    """Read a required string field from an untrusted payload.

    ``str(None)`` would produce the lot id ``"None"`` -- a report that looks real and is about nothing,
    with a request for ``/wafers/None/binmap``. A wrong-typed field is the *event's* data problem, so this
    raises and the processing job records it against the event (``docs/05`` §2).
    """
    value = event.payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{field} must be a non-empty string; got {type(value).__name__} "
            f"for event {event.event_id}"
        )
    return value


@register_report(WAFER_FAILURE_EVENT)
def wafer_failure_report(event: Event, context: ReportContext) -> ReportDefinition:
    """Build the report for one failed wafer.

    The payload arrives from a discoverer and is untrusted: the fields are type-checked here, bound into
    constructor arguments, and escaped by the blocks that print them. A malformed payload raises, which the
    processing job records against this event and retries -- the event's problem, not the run's.
    """
    lot_id = _payload_text(event, "lot_id")
    tool_id = _payload_text(event, "tool_id")
    summary = event.payload.get("summary")

    return ReportDefinition(
        title=f"Failed wafer — {event.event_id}",
        subtitle=summary if isinstance(summary, str) and summary else None,
        event_id=event.event_id,
        metadata={"Lot": lot_id, "Tool": tool_id},
        blocks=[
            WaferMapBlock(lot_id=lot_id),
            MaintenanceHistoryBlock(tool_id=tool_id),
        ],
    )

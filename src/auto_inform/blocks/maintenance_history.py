"""Recent maintenance events for one tool: the worked example of ``docs/02-block-authoring.md`` §7.

Small on purpose. It exists as much to demonstrate the shape of a block as to be useful:

* every parameter it needs arrives through its constructor, not from ambient state;
* every HTTP call carries a ``timeout``, taken from settings;
* a failure the transport could not classify -- "the service says it is degraded" -- is raised as the
  framework's own transient error, so the retry policy can act on it;
* ``render()`` escapes, because the values it prints came from an API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_inform.domain.block import Block
from auto_inform.domain.errors import TransientBlockError
from auto_inform.registry import register_block
from auto_inform.rendering.escaping import html_table

if TYPE_CHECKING:
    from auto_inform.application.context import RenderContext, ReportContext


@dataclass(frozen=True, slots=True)
class MaintenanceRecord:
    """One maintenance event, as the API reports it."""

    performed_at: str
    engineer: str
    summary: str


@register_block
class MaintenanceHistoryBlock(Block[list[MaintenanceRecord]]):
    """Recent maintenance events for one tool.

    The Item text carries the tool id, because a report routinely shows maintenance for several tools
    and two rows named "Maintenance history" would be indistinguishable.
    """

    display_name = "Maintenance history"
    timeout_seconds = 30.0

    def __init__(self, *, tool_id: str, days: int = 30, name: str | None = None) -> None:
        super().__init__(name=name or f"Maintenance history — {tool_id}")
        self._tool_id = tool_id
        self._days = days

    def collect(self, context: ReportContext) -> list[MaintenanceRecord]:
        payload = context.http.get_json(
            f"/maintenance/{self._tool_id}",
            params={"days": self._days},
            timeout=context.settings.block_timeout_seconds,
        )
        if payload.get("status") == "degraded":
            # The transport succeeded; the service is telling us it cannot answer properly yet. That is
            # a transient condition by our own judgement, and only the block can classify it.
            raise TransientBlockError("maintenance service degraded; retry later")

        # A malformed row raises TypeError here, which collection treats as permanent -- a payload shape
        # that a retry will not improve. See docs/02-block-authoring.md §6.3.
        return [MaintenanceRecord(**row) for row in payload["records"]]

    def render(self, data: list[MaintenanceRecord], context: RenderContext) -> str:
        if not data:
            return '<p class="ai-empty">No maintenance recorded in this window.</p>'
        return html_table(
            headers=["Date", "Engineer", "Summary"],
            rows=[(record.performed_at, record.engineer, record.summary) for record in data],
        )

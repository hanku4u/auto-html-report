"""A wafer bin map, rendered as an embedded chart.

Demonstrates the figure rules from ``docs/06-output-html.md`` §5 in a real block: the Agg backend is
pinned by :func:`auto_inform.figure_lock`, the lock is held across creating, drawing, saving and closing
the figure, and the figure is always closed.

Two imports are deliberate. **matplotlib is imported inside ``render()``**, because the registry scan
imports every module in this package -- so a module-level ``pyplot`` import would make every process that
looks a block up pay for matplotlib, including one building a report of HTML tables only, which is the
cost the lazy import exists to avoid. **numpy is imported at module level**, a decision made the other
way: it is a declared direct dependency, it is an order of magnitude cheaper to import, and the type
annotation below needs it. See ``docs/08-packaging.md`` §2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from auto_inform.domain.block import Block
from auto_inform.registry import register_block
from auto_inform.rendering.escaping import escape
from auto_inform.rendering.figures import figure_lock, figure_to_data_uri

if TYPE_CHECKING:
    from auto_inform.application.context import RenderContext, ReportContext


@register_block
class WaferMapBlock(Block[np.ndarray]):
    """The bin map for one lot, as a chart in the Data cell."""

    display_name = "Wafer map"

    def __init__(self, *, lot_id: str, name: str | None = None) -> None:
        super().__init__(name=name or f"Wafer map — {lot_id}")
        self._lot_id = lot_id

    def collect(self, context: ReportContext) -> np.ndarray:
        payload = context.http.get_json(
            f"/wafers/{self._lot_id}/binmap",
            timeout=context.settings.block_timeout_seconds,
        )
        return np.asarray(payload["bins"], dtype=float)

    def render(self, data: np.ndarray, context: RenderContext) -> str:
        # Imported here rather than at module scope: see the module docstring.
        from matplotlib import pyplot

        with figure_lock():
            figure, axes = pyplot.subplots(figsize=(4, 3), dpi=100)
            try:
                axes.imshow(data, cmap="RdYlGn", interpolation="nearest")
                axes.set_title(f"{self._lot_id} bin map")
                figure.tight_layout()
                uri = figure_to_data_uri(figure)
            finally:
                pyplot.close(figure)

        # The lot id is escaped even though it usually arrives from a constructor argument: in practice a
        # report factory fills that argument from an event payload, which is outside data. An f-string
        # inside an attribute is exactly where escaping gets forgotten (docs/06-output-html.md §5).
        return f'<img class="ai-figure" src="{uri}" alt="Wafer bin map for {escape(self._lot_id)}">'

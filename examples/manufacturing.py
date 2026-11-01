"""Semiconductor blocks: a maintenance table and a wafer map chart, sharing one API call per tool or lot.

Needs matplotlib, from the ``charts`` extra.

    uv run python -m examples.manufacturing
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from matplotlib.figure import Figure

from auto_html_report import Block, build_report, escape, figure_to_data_uri, html_table
from examples.demo_api import TOOL_ID
from examples.http_client import UrllibHttpClient
from examples.local_api import local_api


def get_maintenance(tool_id: str, http: Any) -> Any:
    return http.get_json(f"/maintenance/{tool_id}", timeout=10)


def get_bin_map(lot_id: str, http: Any) -> Any:
    return http.get_json(f"/wafers/{lot_id}/binmap", timeout=10)


class MaintenanceHistory(Block):
    title = "Maintenance history"
    gather = get_maintenance

    def render(self, data: Any) -> str:
        if not data["records"]:
            return '<p class="ai-empty">No maintenance recorded.</p>'
        rows = [(r["performed_at"], r["engineer"], r["summary"]) for r in data["records"]]
        return html_table(["Date", "Engineer", "Summary"], rows)


class MaintenanceCount(Block):
    title = "Maintenance count"
    gather = get_maintenance

    def render(self, data: Any) -> str:
        return escape(str(len(data["records"])))


class WaferMap(Block):
    title = "Wafer map"
    gather = get_bin_map

    def render(self, data: Any) -> str:
        figure = Figure(figsize=(4, 3))
        axes = figure.add_subplot()
        axes.imshow(data["bins"], cmap="RdYlGn", interpolation="nearest")
        figure.tight_layout()
        return f'<img class="ai-figure" src="{figure_to_data_uri(figure)}" alt="Wafer bin map">'


def main(output_dir: Path = Path("reports/manufacturing")) -> Path:
    with local_api() as api:
        report = build_report(
            "Failed wafer review",
            [MaintenanceHistory(), MaintenanceCount(), WaferMap()],
            {
                "tool_id": TOOL_ID,
                "lot_id": "L1001",
                "http": UrllibHttpClient(api.url, token=api.token),
            },
            metadata={"Tool": TOOL_ID, "Lot": "L1001"},
        )
        print(f"/maintenance/{TOOL_ID} calls: {api.hits(f'/maintenance/{TOOL_ID}')}")
    path = report.save(output_dir)
    print(f"wrote {path}")
    return path


if __name__ == "__main__":
    main()

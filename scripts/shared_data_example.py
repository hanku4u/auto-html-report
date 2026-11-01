"""A report built from a dynamic list of blocks, where some blocks share one API call.

    uv run python -m scripts.shared_data_example
    uv run python -m scripts.shared_data_example maintenance_table maintenance_count

The first form builds every row, and the second builds only the rows you name. It starts a local test API, so
it needs no real service.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from auto_html_report import Block, build_report, html_table
from examples.demo_api import TOOL_ID
from examples.http_client import UrllibHttpClient
from examples.local_api import local_api

# Step 1: where the data comes from. Each parameter name is a key the context must supply.


def get_maintenance(tool_id: str, http: Any) -> Any:
    """An API call."""
    return http.get_json(f"/maintenance/{tool_id}", timeout=10)


def get_lots(database: Path) -> list[tuple[str, float]]:
    """A database query. It opens its own connection, because it runs on a worker thread."""
    connection = sqlite3.connect(database, timeout=5.0)
    try:
        return connection.execute("SELECT lot, yield FROM lots ORDER BY lot").fetchall()
    finally:
        connection.close()


def get_shifts() -> list[tuple[str, str]]:
    """A plain function."""
    return [("Day", "06:00-14:00"), ("Swing", "14:00-22:00"), ("Night", "22:00-06:00")]


# Step 2: the blocks. Each names its source and says how to show the result.


class MaintenanceTable(Block):
    title = "Maintenance history"
    gather = get_maintenance

    def render(self, data: Any) -> str:
        rows = [(r["performed_at"], r["engineer"], r["summary"]) for r in data["records"]]
        return html_table(["Date", "Engineer", "Summary"], rows)


class MaintenanceCount(Block):
    title = "Maintenance count"
    gather = get_maintenance  # the same function and arguments as above, so only one call

    def render(self, data: Any) -> str:
        return html_table(["Events"], [(len(data["records"]),)])


class LotYields(Block):
    title = "Lot yields"
    gather = get_lots

    def render(self, data: list[tuple[str, float]]) -> str:
        return html_table(["Lot", "Yield"], [(lot, f"{value:.1%}") for lot, value in data])


class Shifts(Block):
    title = "Shift calendar"
    gather = get_shifts

    def render(self, data: list[tuple[str, str]]) -> str:
        return html_table(["Shift", "Hours"], data)


class ToolSummary(Block):
    """One block reading two sources, which it still shares with the others."""

    title = "Tool summary"
    gather: ClassVar[dict[str, Any]] = {"maintenance": get_maintenance, "lots": get_lots}

    def render(self, data: dict[str, Any]) -> str:
        return html_table(
            ["Events", "Lots"], [(len(data["maintenance"]["records"]), len(data["lots"]))]
        )


# Step 3: choose the rows at runtime and build the report.

ROWS: dict[str, type[Block]] = {
    "maintenance_table": MaintenanceTable,
    "maintenance_count": MaintenanceCount,
    "lots": LotYields,
    "shifts": Shifts,
    "tool_summary": ToolSummary,
}


def main(requested: list[str]) -> int:
    unknown = [name for name in requested if name not in ROWS]
    if unknown:
        print(f"unknown rows {unknown}; choose from {sorted(ROWS)}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as scratch, local_api() as api:
        database = Path(scratch) / "lots.sqlite3"
        create_demo_database(database)

        report = build_report(
            title="Tool review",
            blocks=[ROWS[name]() for name in (requested or ROWS)],  # the dynamic list
            context={
                "tool_id": TOOL_ID,
                "http": UrllibHttpClient(api.url, token=api.token),
                "database": database,
            },
        )
        print(f"/maintenance/{TOOL_ID} calls: {api.hits(f'/maintenance/{TOOL_ID}')}")

    print(f"wrote {report.save('reports/shared-data-example')}")
    return 0


def create_demo_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE lots (lot TEXT, yield REAL)")
        connection.executemany(
            "INSERT INTO lots VALUES (?, ?)", [("L1001", 0.912), ("L1002", 0.874)]
        )
    connection.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""The README's quick start: a one-row report with no network.

uv run python examples/quick_start.py /tmp/report-demo
"""

from __future__ import annotations

import sys
from pathlib import Path

from auto_html_report import Block, build_report, html_table


def get_deployment() -> list[tuple[str, str]]:
    return [("Python", sys.version.split()[0]), ("Platform", sys.platform)]


class DeploymentBlock(Block):
    title = "Deployment"
    gather = get_deployment

    def render(self, data: list[tuple[str, str]]) -> str:
        return html_table(["Key", "Value"], data)


def main(output_dir: Path) -> Path:
    path = build_report("Quick start", [DeploymentBlock()]).save(output_dir)
    print(f"wrote {path}")
    return path


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("reports"))

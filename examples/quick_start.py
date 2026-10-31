"""The README's quick start: a one-row report, with no network and no configuration.

    uv run python examples/quick_start.py /tmp/report-demo

Everything here is local: the block returns literal data, so the script runs anywhere and produces a
self-contained HTML file. Swap ``collect()`` for an HTTP call and the rest of the program is unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

from auto_html_report import (
    Block,
    ExitCode,
    LocalDirectoryWriter,
    RenderContext,
    ReportContext,
    ReportDefinition,
    Settings,
    generate_report,
    html_table,
    new_report_context,
)


class DeploymentBlock(Block[list[tuple[str, str]]]):
    """One row of facts about this machine's Python, gathered without I/O."""

    display_name = "Deployment"

    def collect(self, context: ReportContext) -> list[tuple[str, str]]:
        return [
            ("Python", sys.version.split()[0]),
            ("Platform", sys.platform),
        ]

    def render(self, data: list[tuple[str, str]], context: RenderContext) -> str:
        # html_table() escapes every cell, which is the one thing a block author must not forget.
        return html_table(headers=["Key", "Value"], rows=data)


def main(output_dir: Path) -> ExitCode:
    settings = Settings(output_dir=output_dir)
    definition = ReportDefinition(title="Quick start", blocks=[DeploymentBlock()])
    result = generate_report(
        definition,
        context=new_report_context(settings),
        writer=LocalDirectoryWriter(settings.output_dir),
    )
    # A writer was supplied, so the result carries where the document landed.
    assert result.location is not None
    print(f"wrote {result.location.uri}")
    return ExitCode.SUCCESS


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("reports")))

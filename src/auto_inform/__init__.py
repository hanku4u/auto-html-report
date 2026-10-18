"""auto_inform -- dynamic, self-contained HTML report generation.

This module is the public surface: everything a block author, a report factory or a job script should
import. It grew in stages -- the author-facing names in stage 3, the report model and the entry point in
stages 5 and 6 -- and stage 7 onward completes it with the configuration factories, the stores, the job
runners and the exit codes.

Nothing here imports matplotlib or pyplot eagerly: a report built only from HTML tables never pays for
them, and a test in ``tests/unit/test_rendering_figures.py`` pins that in a subprocess.
"""

from __future__ import annotations

from auto_inform.application.context import RenderContext, ReportContext
from auto_inform.application.ports import OutputWriter
from auto_inform.domain.block import Block, BlockOutcome, BlockStatus
from auto_inform.domain.errors import (
    AutoInformError,
    BlockError,
    ConfigurationError,
    OutputError,
    PermanentBlockError,
    StoreError,
    TransientBlockError,
)
from auto_inform.domain.report import (
    OutputLocation,
    Report,
    ReportDefinition,
    ReportResult,
    ReportRow,
)
from auto_inform.generate import generate_report, report_filename
from auto_inform.infrastructure.writers.local_writer import LocalDirectoryWriter
from auto_inform.rendering.escaping import escape, html_table
from auto_inform.rendering.figures import figure_lock, figure_to_data_uri

# Deliberately flat and alphabetical. The names fall into groups -- writing a block, building a report,
# producing the document, and classifying a failure -- but the ordering carries no meaning, because an
# unsorted __all__ is a lint error in this project.
__all__ = [
    "AutoInformError",
    "Block",
    "BlockError",
    "BlockOutcome",
    "BlockStatus",
    "ConfigurationError",
    "LocalDirectoryWriter",
    "OutputError",
    "OutputLocation",
    "OutputWriter",
    "PermanentBlockError",
    "RenderContext",
    "Report",
    "ReportContext",
    "ReportDefinition",
    "ReportResult",
    "ReportRow",
    "StoreError",
    "TransientBlockError",
    "escape",
    "figure_lock",
    "figure_to_data_uri",
    "generate_report",
    "html_table",
    "report_filename",
]

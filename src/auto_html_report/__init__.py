"""auto_html_report -- dynamic, self-contained HTML report generation.

This module is the public surface: everything a block author, a report factory or a job script should
import. It grew in stages -- the author-facing names in stage 3, the report model and entry point in
stages 5 and 6, the configuration factories in stage 7, the event store and discovery job in stage 8,
the processing job and exit codes in stage 9, and the report family and examples in stage 10.
Stage 11 added the optional PostgreSQL store and S3 writer, which import their drivers only when
constructed.

Nothing here imports matplotlib or pyplot eagerly: a report built only from HTML tables never pays for
them, and a test in ``tests/unit/test_rendering_figures.py`` pins that in a subprocess.
"""

from __future__ import annotations

from auto_html_report._version import get_version
from auto_html_report.application.collect_blocks import collect_blocks
from auto_html_report.application.context import DiscoveryContext, RenderContext, ReportContext
from auto_html_report.application.discovery import (
    Discoverer,
    DiscoveryOutcome,
    DiscoveryRun,
    run_discovery,
)
from auto_html_report.application.ports import (
    EventStore,
    FailureRecord,
    OutputWriter,
    SaveResult,
)
from auto_html_report.domain.block import Block, BlockOutcome, BlockStatus
from auto_html_report.domain.errors import (
    AutoHtmlReportError,
    BlockError,
    ConfigurationError,
    OutputError,
    PermanentBlockError,
    StoreError,
    TransientBlockError,
)
from auto_html_report.domain.event import Event, EventStatus
from auto_html_report.domain.report import (
    OutputLocation,
    Report,
    ReportDefinition,
    ReportResult,
    ReportRow,
)
from auto_html_report.exit_codes import ExitCode, exit_code_for
from auto_html_report.generate import generate_report, report_filename
from auto_html_report.infrastructure.clock import SystemClock
from auto_html_report.infrastructure.factories import (
    build_output_writer,
    new_discovery_context,
    new_report_context,
)
from auto_html_report.infrastructure.logging_setup import configure_logging
from auto_html_report.infrastructure.settings import Settings, load_settings
from auto_html_report.infrastructure.stores.postgres_store import PostgresEventStore
from auto_html_report.infrastructure.stores.sqlite_store import SqliteEventStore
from auto_html_report.infrastructure.writers.local_writer import LocalDirectoryWriter
from auto_html_report.infrastructure.writers.s3_writer import S3Writer
from auto_html_report.processing import (
    EventProcessingOutcome,
    ProcessingRun,
    run_processing,
)
from auto_html_report.registry import register_report
from auto_html_report.rendering.escaping import escape, html_table
from auto_html_report.rendering.figures import figure_lock, figure_to_data_uri
from auto_html_report.rendering.shell import render_report

# Deliberately flat and alphabetical. The names fall into groups -- writing a block, building a report,
# producing the document, configuring it, and classifying a failure -- but the ordering carries no
# meaning, because an unsorted __all__ is a lint error in this project.
#: The installed version, computed once at import from the distribution metadata. ``get_version()``
#: remains the source (``docs/08-packaging.md`` §7); this is the attribute a consumer reaches for first.
__version__ = get_version()


__all__ = [
    "AutoHtmlReportError",
    "Block",
    "BlockError",
    "BlockOutcome",
    "BlockStatus",
    "ConfigurationError",
    "Discoverer",
    "DiscoveryContext",
    "DiscoveryOutcome",
    "DiscoveryRun",
    "Event",
    "EventProcessingOutcome",
    "EventStatus",
    "EventStore",
    "ExitCode",
    "FailureRecord",
    "LocalDirectoryWriter",
    "OutputError",
    "OutputLocation",
    "OutputWriter",
    "PermanentBlockError",
    "PostgresEventStore",
    "ProcessingRun",
    "RenderContext",
    "Report",
    "ReportContext",
    "ReportDefinition",
    "ReportResult",
    "ReportRow",
    "S3Writer",
    "SaveResult",
    "Settings",
    "SqliteEventStore",
    "StoreError",
    "SystemClock",
    "TransientBlockError",
    "__version__",
    "build_output_writer",
    "collect_blocks",
    "configure_logging",
    "escape",
    "exit_code_for",
    "figure_lock",
    "figure_to_data_uri",
    "generate_report",
    "html_table",
    "load_settings",
    "new_discovery_context",
    "new_report_context",
    "register_report",
    "render_report",
    "report_filename",
    "run_discovery",
    "run_processing",
]

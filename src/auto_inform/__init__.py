"""auto_inform -- dynamic, self-contained HTML report generation.

This module is the public surface: everything a block author, a report factory or a job script should
import. It grew in stages -- the author-facing names in stage 3, the report model and entry point in
stages 5 and 6, the configuration factories in stage 7, and the event store and discovery job in
stage 8. Stage 9 completes it with the processing job and the exit codes, which is why a job script
written today still needs two names from ``docs/05`` §6 that have not landed yet.

Nothing here imports matplotlib or pyplot eagerly: a report built only from HTML tables never pays for
them, and a test in ``tests/unit/test_rendering_figures.py`` pins that in a subprocess.
"""

from __future__ import annotations

from auto_inform.application.context import DiscoveryContext, RenderContext, ReportContext
from auto_inform.application.discovery import (
    Discoverer,
    DiscoveryOutcome,
    DiscoveryRun,
    run_discovery,
)
from auto_inform.application.ports import EventStore, OutputWriter, SaveResult
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
from auto_inform.domain.event import Event, EventStatus
from auto_inform.domain.report import (
    OutputLocation,
    Report,
    ReportDefinition,
    ReportResult,
    ReportRow,
)
from auto_inform.generate import generate_report, report_filename
from auto_inform.infrastructure.clock import SystemClock
from auto_inform.infrastructure.factories import (
    build_output_writer,
    new_discovery_context,
    new_report_context,
)
from auto_inform.infrastructure.logging_setup import configure_logging
from auto_inform.infrastructure.settings import Settings, load_settings
from auto_inform.infrastructure.stores.sqlite_store import SqliteEventStore
from auto_inform.infrastructure.writers.local_writer import LocalDirectoryWriter
from auto_inform.registry import register_report
from auto_inform.rendering.escaping import escape, html_table
from auto_inform.rendering.figures import figure_lock, figure_to_data_uri

# Deliberately flat and alphabetical. The names fall into groups -- writing a block, building a report,
# producing the document, configuring it, and classifying a failure -- but the ordering carries no
# meaning, because an unsorted __all__ is a lint error in this project.
__all__ = [
    "AutoInformError",
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
    "EventStatus",
    "EventStore",
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
    "SaveResult",
    "Settings",
    "SqliteEventStore",
    "StoreError",
    "SystemClock",
    "TransientBlockError",
    "build_output_writer",
    "configure_logging",
    "escape",
    "figure_lock",
    "figure_to_data_uri",
    "generate_report",
    "html_table",
    "load_settings",
    "new_discovery_context",
    "new_report_context",
    "register_report",
    "report_filename",
    "run_discovery",
]

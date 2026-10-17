"""auto_inform -- dynamic, self-contained HTML report generation.

This module is the public surface: everything a block author or a job script should import. It came
into existence in stage 3 rather than stage 6, because the block-authoring examples in
``docs/02`` and ``docs/06`` import from here and stage 4 writes blocks from those examples. Stage 6
completes the surface with the writers, the stores, the job runners and the exit codes.

Nothing here imports matplotlib or pyplot eagerly: a report built only from HTML tables never pays for
them, and a test in ``tests/unit/test_rendering_figures.py`` pins that in a subprocess.
"""

from __future__ import annotations

from auto_inform.application.context import RenderContext, ReportContext
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
from auto_inform.rendering.escaping import escape, html_table
from auto_inform.rendering.figures import figure_lock, figure_to_data_uri

# Deliberately flat and alphabetical. The names fall into three groups -- writing a block (Block and
# its context types), rendering a block's data (the escaping and figure helpers), and classifying a
# failure (the error classes a block may raise) -- but the ordering carries no meaning, because an
# unsorted __all__ is a lint error in this project.
__all__ = [
    "AutoInformError",
    "Block",
    "BlockError",
    "BlockOutcome",
    "BlockStatus",
    "ConfigurationError",
    "OutputError",
    "PermanentBlockError",
    "RenderContext",
    "ReportContext",
    "StoreError",
    "TransientBlockError",
    "escape",
    "figure_lock",
    "figure_to_data_uri",
    "html_table",
]

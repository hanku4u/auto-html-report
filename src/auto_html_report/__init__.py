"""auto_html_report: turn a list of blocks into one self-contained HTML report.

The documentation is installed with the package, in the ``docs`` folder next to this file: ``README.md`` is the
usage guide, and ``design.md`` explains how the package works and why. To read the guide from Python::

    from importlib.resources import files

    print(files("auto_html_report").joinpath("docs", "README.md").read_text(encoding="utf-8"))
"""

from __future__ import annotations

from auto_html_report._version import get_version
from auto_html_report.block import Block
from auto_html_report.errors import FetchError, MissingContextError, ReportError, TransientError
from auto_html_report.rendering import escape, figure_to_data_uri, html_table
from auto_html_report.report import Report, Row, build_report

__version__ = get_version()

__all__ = [
    "Block",
    "FetchError",
    "MissingContextError",
    "Report",
    "ReportError",
    "Row",
    "TransientError",
    "__version__",
    "build_report",
    "escape",
    "figure_to_data_uri",
    "html_table",
]

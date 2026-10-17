"""Report data types: the definition, the assembled report, and the result of writing it.

The split between :class:`ReportDefinition` (what to build), :class:`Report` (what was built) and
:class:`ReportResult` (where it was written) is deliberate. Rendering is a pure function of a
:class:`Report`, so a caller can inspect outcomes before deciding to write anything, or render into
an email body instead of a file. See ``docs/03-report-api.md`` §1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from auto_inform.domain.block import Block, BlockOutcome, BlockStatus
from auto_inform.domain.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class OutputLocation:
    """Where a written report ended up.

    A pure value type, which is why it lives in the domain even though only writers produce it: the
    filename is computed once, by the caller, and a writer merely reports where it put the bytes.
    See ``docs/06-output-html.md`` §1 and §2.
    """

    kind: str
    uri: str


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    """What to build: a title, an ordered list of instantiated blocks, and optional framing.

    Row order is the order of ``blocks``, always. That is a display guarantee and never a
    dependency mechanism -- if two blocks need the same data, a report factory fetches it once and
    injects it into both, rather than relying on which row is rendered first.
    """

    title: str
    blocks: Sequence[Block[Any]]
    subtitle: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Fail fast, and report every problem rather than only the first: a definition is
        # constructed by hand in code, so a mistake is cheap to fix once it is visible.
        if not self.blocks:
            raise ConfigurationError(f"report {self.title!r} contains no blocks")

        seen: set[str] = set()
        duplicates: set[str] = set()
        for block in self.blocks:
            name = block.name
            if name in seen:
                duplicates.add(name)
            seen.add(name)
        if duplicates:
            raise ConfigurationError(
                f"report {self.title!r} has duplicate block names: {sorted(duplicates)}. "
                "Names fill the Item column, so a reader cannot tell duplicate rows apart; "
                "pass name=... to distinguish repeated blocks."
            )


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One assembled row. ``html`` is the *inner* HTML of the Data cell, not a row or a table.

    There is deliberately no ``check`` field: the Check column is structural, emitted empty by the
    renderer for every row. A field that can only ever hold ``""`` is bloat. See ADR-0018.
    """

    item: str
    html: str
    outcome: BlockOutcome


@dataclass(frozen=True, slots=True)
class Report:
    """What was built, in the order it will be displayed."""

    definition: ReportDefinition
    rows: tuple[ReportRow, ...]
    generated_at: datetime
    run_id: str

    @property
    def all_succeeded(self) -> bool:
        """True when every row is ``OK``. An empty row set counts as success, vacuously."""
        return all(row.outcome.status is BlockStatus.OK for row in self.rows)

    @property
    def failed_rows(self) -> tuple[ReportRow, ...]:
        """Rows that are not ``OK``, in display order. Drives the non-zero exit code."""
        return tuple(row for row in self.rows if row.outcome.status is not BlockStatus.OK)


@dataclass(frozen=True, slots=True)
class ReportResult:
    """The outcome of :func:`auto_inform.generate_report`.

    ``location`` is ``None`` when no writer was supplied -- that is the "give me the HTML and let me
    decide" case, and it is not an error.
    """

    report: Report
    html: str
    location: OutputLocation | None

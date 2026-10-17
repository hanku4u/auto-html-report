"""The Block contract: one row of a report.

A block splits its work in two so that rendering stays trivially testable: :meth:`Block.collect`
may perform I/O and may fail; :meth:`Block.render` is pure. See ``docs/02-block-authoring.md`` for
the author-facing rules, and ``docs/02`` §6 for what the framework does *to* a block — timeouts,
retries, failure isolation and ordering.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

if TYPE_CHECKING:
    # Type-only, deliberately: the domain layer must be importable without the application or
    # infrastructure layers existing. See docs/01-overview.md §4, rule 1.
    from auto_inform.application.context import RenderContext, ReportContext

DataT = TypeVar("DataT")


class BlockStatus(StrEnum):
    """How a block's collection ended. Anything but ``OK`` puts an error cell in the report."""

    OK = "OK"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True, slots=True)
class BlockOutcome:
    """The framework's record of one block's attempt.

    ``error_type`` and ``error_message`` are safe to display: the message is truncated and is
    never a traceback. Tracebacks go to the log, because a report gets forwarded by email to
    people who should not be reading stack frames.
    """

    name: str
    status: BlockStatus
    duration_seconds: float
    attempts: int
    error_type: str | None = None
    error_message: str | None = None


class Block(ABC, Generic[DataT]):
    """One row of a report.

    Subclasses split the work in two so that rendering stays trivially testable::

        collect() -> I/O, allowed to be slow and to fail
        render()  -> pure transformation, no I/O, no clock, no randomness

    ``DataT`` is opaque to the framework: it is typed end to end for the author's benefit only, and
    is never inspected, serialised, or passed between blocks.
    """

    #: Default text for the "Item" column. Overridable per instance via ``name=``.
    display_name: ClassVar[str] = ""

    #: Per-block overrides; ``None`` means "use the framework default from Settings".
    timeout_seconds: ClassVar[float | None] = None
    max_retries: ClassVar[int | None] = None

    def __init__(self, *, name: str | None = None) -> None:
        resolved = name or self.display_name
        if not resolved:
            raise ValueError(f"{type(self).__name__} must set display_name or pass name=...")
        self._name = resolved

    @property
    def name(self) -> str:
        """Text shown in the Item column. Must be unique within a report.

        A property rather than a bare class attribute because the same block type is frequently
        instantiated several times in one report with different parameters -- "Maintenance -- T-104"
        against "Maintenance -- T-112". A static ``display_name`` keeps the simple case a one-liner.
        """
        return self._name

    @abstractmethod
    def collect(self, context: ReportContext) -> DataT:
        """Gather this block's data. Runs in a worker thread; I/O is permitted.

        Must honour its timeout: the framework cannot interrupt a running thread, so a block that
        omits ``timeout=`` on its HTTP calls will outlive its own budget.
        """

    @abstractmethod
    def render(self, data: DataT, context: RenderContext) -> str:
        """Return the inner HTML for this block's Data cell. Pure.

        No I/O, no clock, no randomness, no logging, and no mutation of the input. Must escape
        anything it interpolates from outside the process -- see ``docs/06-output-html.md`` §4.
        """

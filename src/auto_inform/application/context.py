"""The frozen contexts handed to blocks, discoverers and jobs.

These live here rather than in ``domain/`` because they bundle injected dependencies -- ``Settings``
from the infrastructure layer, and the ``HttpClient`` and ``Clock`` ports. ``domain/block.py`` names
two of them in its method signatures, importing them under ``TYPE_CHECKING`` only, so the domain
layer keeps no *runtime* dependency on this module. See ``docs/01-overview.md`` §4 rule 1 and
``docs/03-report-api.md`` §5.

All four are frozen, and therefore safe to share with every worker thread in a report. That is a
contract, not an accident: anything mutable added here would need to be thread-safe and documented
as such. The v1 answer is "don't" -- use per-instance state on the block, which the framework
guarantees is used by one thread at a time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from auto_inform.application.ports import Clock, HttpClient
from auto_inform.infrastructure.settings import Settings


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """Identity of one job run, generated once per context."""

    run_id: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Everything a block's ``collect()`` is allowed to depend on."""

    settings: Settings
    http: HttpClient
    logger: logging.Logger
    clock: Clock
    run: RunMetadata


@dataclass(frozen=True, slots=True)
class RenderContext:
    """What ``render()`` is allowed to touch.

    Slimmer than :class:`ReportContext` on purpose: no HTTP, no store, and **no logger**, because
    ``render()`` is pure and logging is a side effect. A block that needs to log does it in
    ``collect()``.
    """

    generated_at: datetime
    report_title: str


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    """Everything a discoverer's ``discover()`` is allowed to depend on."""

    settings: Settings
    http: HttpClient
    logger: logging.Logger
    clock: Clock
    run: RunMetadata

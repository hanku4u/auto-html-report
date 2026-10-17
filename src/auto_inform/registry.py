"""The registry: block classes and report factories, looked up by name.

Deliberately small. Blocks are added to this package directly and never shipped from elsewhere, so there
are no ``entry_points`` and no import hooks -- but the *interface* stays plugin-shaped, so a future
decision to load external packages changes only this module (ADR-0003). See
``docs/03-report-api.md`` §4.

Two rules make this file worth reading carefully:

* **Lookups are lazy.** ``import_builtin_blocks()`` runs on the first lookup, not at import time, so a
  caller who only ever passes explicit block instances never pays for scanning the block package.
* **Registration is idempotent under re-import, but not under collision.** Re-registering the *same*
  class is a no-op -- which matters because a ``pkgutil`` scan is easy to trigger twice in a long-lived
  process. A *different* class claiming a key that is already taken is an error, because it means
  somebody copy-pasted a block and forgot to rename it.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from auto_inform.application.context import ReportContext
from auto_inform.domain.block import Block
from auto_inform.domain.errors import ConfigurationError
from auto_inform.domain.event import Event
from auto_inform.domain.report import ReportDefinition

#: Knows how to build the report for one event type. See `docs/03-report-api.md` §3.
ReportFactory = Callable[[Event, ReportContext], ReportDefinition]

_blocks: dict[str, type[Block[Any]]] = {}
_report_factories: dict[str, ReportFactory] = {}
_builtin_blocks_imported = False


def register_block(cls: type[Block[Any]]) -> type[Block[Any]]:
    """Class decorator: register a block under its ``display_name``.

    The key is the class-level ``display_name`` rather than the instance's ``name``, because the
    registry exists for *lookup by type* -- "give me the maintenance-history block" -- while the
    per-instance name exists to tell two rows of one type apart.
    """
    key = cls.display_name
    if not key:
        raise ConfigurationError(
            f"{cls.__name__} cannot be registered: it sets no display_name to key on. A block that "
            "names itself only per instance must be passed to a report definition explicitly."
        )

    registered = _blocks.get(key)
    if registered is not None and registered is not cls:
        raise ConfigurationError(
            f"two different blocks are registered as {key!r}: {registered.__name__} and "
            f"{cls.__name__}. A duplicate key means a block was copied and not renamed, and a "
            "mystery row in a report is harder to diagnose than this."
        )

    _blocks[key] = cls
    return cls


def import_builtin_blocks() -> None:
    """Import every module in ``auto_inform.blocks`` so that their decorators run.

    Idempotent: the scan is recorded as done, so a second lookup in the same process is free. Called
    lazily by the lookups below rather than at import time.
    """
    global _builtin_blocks_imported
    if _builtin_blocks_imported:
        return

    from auto_inform import blocks as blocks_package

    for module in pkgutil.iter_modules(blocks_package.__path__):
        importlib.import_module(f"{blocks_package.__name__}.{module.name}")

    _builtin_blocks_imported = True


def get_block(key: str) -> type[Block[Any]]:
    """The block class registered as ``key``, scanning the built-ins first if that has not happened."""
    import_builtin_blocks()
    try:
        return _blocks[key]
    except KeyError:
        raise ConfigurationError(
            f"no block is registered as {key!r}. Registered: {sorted(_blocks)}. Note that a block "
            "defined outside auto_inform.blocks registers only once its module has been imported "
            "(docs/03-report-api.md §4)."
        ) from None


def available_blocks() -> Mapping[str, type[Block[Any]]]:
    """Every registered block, keyed by ``display_name``. A read-only view of the registry."""
    import_builtin_blocks()
    return MappingProxyType(dict(_blocks))


def register_report(event_type: str) -> Callable[[ReportFactory], ReportFactory]:
    """Decorator factory: register the report factory for an event type.

    The factory receives the whole event, so two events of one type can build entirely different
    reports. That is what makes "another event needs a completely different context" work without a
    second mechanism (``docs/03-report-api.md`` §3).
    """

    def _register(factory: ReportFactory) -> ReportFactory:
        registered = _report_factories.get(event_type)
        if registered is not None and registered is not factory:
            raise ConfigurationError(
                f"a report factory is already registered for {event_type!r}: {registered.__name__}"
            )
        _report_factories[event_type] = factory
        return factory

    return _register


def get_report_factory(event_type: str) -> ReportFactory:
    """The factory registered for ``event_type``, or a ``ConfigurationError`` naming what is known.

    Note that nothing scans a ``reports`` package: a factory registers itself when its module is
    imported, so a job script imports the modules it needs. The error message says so, because
    "no factory for this type" and "the module was never imported" look identical from here.
    """
    try:
        return _report_factories[event_type]
    except KeyError:
        raise ConfigurationError(
            f"no report factory is registered for event type {event_type!r}. Registered: "
            f"{sorted(_report_factories)}. A factory registers on import, so a job script must import "
            "the module that defines it (docs/05-discovery-and-processing.md §6)."
        ) from None

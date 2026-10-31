"""The registry: block classes and report factories, looked up by name.

Deliberately small. Blocks are added to this package directly and never shipped from elsewhere, so there
are no ``entry_points`` and no import hooks -- but the *interface* stays plugin-shaped, so a future
decision to load external packages changes only this module (ADR-0003). See
``docs/03-report-api.md`` §4.

Four rules make this file worth reading carefully:

* **Lookups are lazy.** ``import_builtin_blocks()`` runs on the first lookup, not at import time, so a
  caller who only ever passes explicit block instances never pays for scanning the block package.
* **Registration is idempotent under re-import, but not under collision.** Re-registering the *same*
  class is a no-op -- which matters because a ``pkgutil`` scan is easy to trigger twice in a long-lived
  process. A *different* class claiming a key that is already taken is an error, because it means
  somebody copy-pasted a block and forgot to rename it.
* **The scan is recursive.** A block added inside a subpackage of ``blocks/`` is found, not silently
  skipped because it was one directory deeper than the scan looked.
* **Everything here is thread-safe.** The tables and the scan flag are guarded, because a lookup can
  happen on a worker thread once reports are built concurrently (ADR-0020). The guard is re-entrant, and
  it has to be: the scan calls ``importlib.import_module``, which runs each block module's decorator,
  which calls :func:`register_block` -- on the same thread, while the lock is held.
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from auto_html_report.application.context import ReportContext
from auto_html_report.domain.block import Block
from auto_html_report.domain.errors import ConfigurationError
from auto_html_report.domain.event import Event
from auto_html_report.domain.report import ReportDefinition

#: Knows how to build the report for one event type. See `docs/03-report-api.md` §3.
ReportFactory = Callable[[Event, ReportContext], ReportDefinition]

_registry_lock = threading.RLock()
_blocks: dict[str, type[Block[Any]]] = {}
_report_factories: dict[str, ReportFactory] = {}
_builtin_blocks_imported = False


def register_block(cls: type[Block[Any]]) -> type[Block[Any]]:
    """Class decorator: register a block under its ``display_name``.

    The key is the class-level ``display_name`` rather than the instance's ``name``, because the
    registry exists for *lookup by type* -- "give me the maintenance-history block" -- while the
    per-instance name exists to tell two rows of one type apart.
    """
    if not (isinstance(cls, type) and issubclass(cls, Block)):
        # The annotation says this cannot happen, and at runtime that annotation is a promise nobody
        # checked: a client's untyped decorator target would otherwise register and fail later, at
        # collect(), where the cause is far away from the mistake.
        raise ConfigurationError(f"{cls!r} is not a Block subclass, so it cannot be registered")

    key = cls.display_name
    if not key:
        raise ConfigurationError(
            f"{cls.__name__} cannot be registered: it sets no display_name to key on. A block that "
            "names itself only per instance must be passed to a report definition explicitly."
        )

    with _registry_lock:
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
    """Import every module in ``auto_html_report.blocks``, recursively, so that their decorators run.

    Idempotent: the scan is recorded as done, so a second lookup in the same process is free. Called
    lazily by the lookups below rather than at import time.
    """
    global _builtin_blocks_imported

    with _registry_lock:
        if _builtin_blocks_imported:
            return

        from auto_html_report import blocks as blocks_package

        for module in pkgutil.walk_packages(
            blocks_package.__path__, prefix=f"{blocks_package.__name__}."
        ):
            importlib.import_module(module.name)

        _builtin_blocks_imported = True


def get_block(key: str) -> type[Block[Any]]:
    """The block class registered as ``key``, scanning the built-ins first if that has not happened."""
    import_builtin_blocks()
    with _registry_lock:
        try:
            return _blocks[key]
        except KeyError:
            known = sorted(_blocks)

    raise ConfigurationError(
        f"no block is registered as {key!r}. Registered: {known}. Note that a block "
        "defined outside auto_html_report.blocks registers only once its module has been imported "
        "(docs/03-report-api.md §4)."
    )


def available_blocks() -> Mapping[str, type[Block[Any]]]:
    """Every registered block, keyed by ``display_name``. A read-only snapshot of the registry."""
    import_builtin_blocks()
    with _registry_lock:
        return MappingProxyType(dict(_blocks))


def register_report(event_type: str) -> Callable[[ReportFactory], ReportFactory]:
    """Decorator factory: register the report factory for an event type.

    The factory receives the whole event, so two events of one type can build entirely different
    reports. That is what makes "another event needs a completely different context" work without a
    second mechanism (``docs/03-report-api.md`` §3).
    """

    def _register(factory: ReportFactory) -> ReportFactory:
        with _registry_lock:
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
    with _registry_lock:
        try:
            return _report_factories[event_type]
        except KeyError:
            known = sorted(_report_factories)

    raise ConfigurationError(
        f"no report factory is registered for event type {event_type!r}. Registered: "
        f"{known}. A factory registers on import, so a job script must import "
        "the module that defines it (docs/05-discovery-and-processing.md §6)."
    )

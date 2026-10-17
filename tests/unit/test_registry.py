"""The registry: block lookup, the built-in scan, and the report-factory table.

The registry is process-global by design -- a block registers when its module is imported -- so this
module restores it around every test. Without that, a test that registers a temporary block would leak it
into every later test in the same process, which hermeticity rule 4 of ``docs/09`` §2 forbids.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from auto_inform import registry
from auto_inform.domain.block import Block
from auto_inform.domain.errors import ConfigurationError
from auto_inform.domain.event import Event
from auto_inform.domain.report import ReportDefinition
from tests.support.clocks import DEFAULT_START
from tests.support.contexts import make_report_context
from tests.support.http import FakeHttpClient

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FirstBlock(Block[int]):
    display_name = "First block"

    def collect(self, context: object) -> int:
        return 1

    def render(self, data: int, context: object) -> str:
        return str(data)


class _RivalBlock(Block[int]):
    """A copy-paste of the one above whose author forgot to rename it -- the case the registry guards."""

    display_name = "First block"

    def collect(self, context: object) -> int:
        return 2

    def render(self, data: int, context: object) -> str:
        return str(data)


class _UnnamedBlock(Block[int]):
    """No display_name, so nothing to key on."""

    def collect(self, context: object) -> int:
        return 3

    def render(self, data: int, context: object) -> str:
        return ""


@pytest.fixture(autouse=True)
def _restore_registries() -> Iterator[None]:
    """Snapshot and restore the registry's globals around each test in this module.

    Reaching into private state is the ugly part of testing a global registry; the alternative -- tests
    that depend on the order they run in, or that leak a fake block into the real one -- is worse.

    **The scan is forced before the snapshot**, so what gets restored is a *populated* registry rather
    than an empty one. That detail is the whole fix, and it is not obvious: restoring the tables plus the
    scan flag looks like it should work, but a re-scan cannot repopulate a wiped table, because the block
    modules are already in ``sys.modules`` -- ``import_module`` returns them from cache and their
    decorators never run again. Snapshotting after the scan sidesteps that entirely, which is why the
    flag needs no restoring: it stays ``True``, consistent with the built-ins being present.

    Getting this wrong is invisible from inside the suite: ``test_blocks_*`` sorts before
    ``test_registry`` and has already populated the registry by the time the first snapshot is taken, so
    only a run of this file on its own shows the problem.
    ``test_this_module_should_pass_when_run_on_its_own`` below is the guard.
    """
    registry.import_builtin_blocks()
    blocks = dict(registry._blocks)
    factories = dict(registry._report_factories)
    yield
    registry._blocks.clear()
    registry._blocks.update(blocks)
    registry._report_factories.clear()
    registry._report_factories.update(factories)


# --- registering blocks -------------------------------------------------------


def test_register_block_should_return_the_class_so_it_can_be_used_as_a_decorator() -> None:
    # given a block class
    # when it is registered
    registered = registry.register_block(_FirstBlock)

    # then the decorator hands the class straight back, so `@register_block` leaves the name usable
    assert registered is _FirstBlock


def test_register_block_should_key_the_block_on_its_display_name() -> None:
    # given a registered block
    registry.register_block(_FirstBlock)

    # when it is looked up
    # then the key is the class-level display_name -- the registry answers "give me the
    # maintenance-history block", while an instance's name tells two rows of one type apart
    assert registry.get_block("First block") is _FirstBlock


def test_register_block_for_the_same_class_twice_should_be_idempotent() -> None:
    # given a block registered once
    registry.register_block(_FirstBlock)

    # when the same class registers again, as it does when a module is imported twice
    registry.register_block(_FirstBlock)

    # then it is a no-op rather than an error: a pkgutil scan is easy to trigger twice in a long-lived
    # process, and failing on that would make the registry unusable
    assert registry.get_block("First block") is _FirstBlock


def test_register_block_with_a_key_already_taken_should_raise_configuration_error() -> None:
    # given two different blocks that claim the same display_name
    registry.register_block(_FirstBlock)

    # when the second registers
    # then it is refused, naming both. This is the copy-paste-and-forget-to-rename case, and a mystery
    # row in a report is harder to diagnose than an error at import
    with pytest.raises(ConfigurationError, match="two different blocks"):
        registry.register_block(_RivalBlock)


def test_register_block_without_a_display_name_should_raise_configuration_error() -> None:
    # given a block with no class-level key
    # when it is registered
    # then it is refused, with a message pointing at the alternative -- such a block is usable, but only
    # by passing instances explicitly
    with pytest.raises(ConfigurationError, match="no display_name"):
        registry.register_block(_UnnamedBlock)


# --- the built-in scan --------------------------------------------------------


def test_import_builtin_blocks_should_be_idempotent() -> None:
    # given the scan has run
    registry.import_builtin_blocks()
    after_first = registry.available_blocks()

    # when it runs again
    registry.import_builtin_blocks()

    # then nothing changes and nothing raises, which is what makes it safe to call lazily on every lookup
    assert registry.available_blocks() == after_first


def test_available_blocks_should_include_the_builtin_blocks() -> None:
    # given the built-in block package
    # when the registry is asked what exists
    # then the scan has imported both shipped blocks and keyed them by display_name
    available = registry.available_blocks()

    assert "Maintenance history" in available
    assert "Wafer map" in available


def test_get_block_should_find_a_builtin_block_by_display_name() -> None:
    # given a lookup by the documented key
    # then it resolves without the caller having imported anything itself -- the scan is lazy, not
    # something the caller has to remember
    assert registry.get_block("Wafer map").display_name == "Wafer map"


def test_get_block_with_an_unknown_key_should_raise_configuration_error() -> None:
    # given a key nothing is registered under
    # when it is looked up
    # then the error lists what is registered and explains the import-order trap, because "no such
    # block" and "the module was never imported" look identical from here
    with pytest.raises(ConfigurationError, match="Registered:.*Wafer map"):
        registry.get_block("No such block")


def test_available_blocks_should_be_a_read_only_view() -> None:
    # given the registry's answer
    available = registry.available_blocks()

    # when a caller tries to add to it
    # then it refuses: handing out the live dictionary would let one report's registration mutate
    # another's view of what exists
    with pytest.raises(TypeError):
        available["Injected"] = _FirstBlock  # type: ignore[index]


# --- report factories ---------------------------------------------------------


def _factory(_event: object, _context: object) -> ReportDefinition:
    return ReportDefinition(title="From the factory", blocks=[_FirstBlock()])


def _rival_factory(_event: object, _context: object) -> ReportDefinition:
    return ReportDefinition(title="From the rival", blocks=[_FirstBlock()])


def test_register_report_should_make_the_factory_lookup_work() -> None:
    # given a factory registered for an event type
    registry.register_report("WAFER_FAILURE")(_factory)

    # when the type is looked up
    # then the same factory comes back, which is how the processing job builds the right report
    assert registry.get_report_factory("WAFER_FAILURE") is _factory


def test_register_report_should_return_the_factory_so_it_can_be_used_as_a_decorator() -> None:
    # given a factory
    # when it is registered
    # then the decorator hands it back, so `@register_report("TYPE")` leaves the function usable
    assert registry.register_report("MAINTENANCE_OVERDUE")(_factory) is _factory


def test_register_report_for_the_same_type_twice_should_raise_configuration_error() -> None:
    # given a type that already has a factory
    registry.register_report("WAFER_FAILURE")(_factory)

    # when a different factory claims it
    # then it is refused, naming the incumbent: two factories for one event type is a deployment bug,
    # not a preference
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register_report("WAFER_FAILURE")(_rival_factory)


def test_get_report_factory_with_an_unknown_type_should_raise_configuration_error() -> None:
    # given an event type nothing handles
    # when the lookup fails
    # then the message says a factory registers on import, so a job script must import its module --
    # otherwise this looks like a missing factory rather than a missing import
    with pytest.raises(ConfigurationError, match="registers on import"):
        registry.get_report_factory("NEVER_HEARD_OF_IT")


def test_the_report_factory_should_be_callable_with_an_event_and_a_context() -> None:
    # given a registered factory
    registry.register_report("WAFER_FAILURE")(_factory)
    event = Event(
        event_type="WAFER_FAILURE",
        event_id="WAFER_FAILURE:LOT-88213",
        payload={"lot_id": "LOT-88213"},
        discovered_at=DEFAULT_START,
        source="test",
    )

    # when it is called the way processing calls it
    factory = registry.get_report_factory("WAFER_FAILURE")
    definition = factory(event, make_report_context(http=FakeHttpClient()))

    # then it returns a report definition, which is the whole contract of a factory
    assert definition.title == "From the factory"


# --- isolation and concurrency, the two properties the tables make easy to get wrong ---


def test_a_repeated_scan_should_not_repopulate_a_wiped_table() -> None:
    # given a fully scanned registry whose block table is then emptied, as the isolation fixture does
    registry.import_builtin_blocks()
    registry._blocks.clear()

    # when the scan is asked to run again
    registry.import_builtin_blocks()

    # then the table stays empty. The block modules are already in sys.modules, so import_module returns
    # them from cache and their decorators never run again -- a wiped table cannot be refilled by
    # re-scanning. A characterisation test rather than a wish: it is why the isolation fixture snapshots a
    # *populated* registry instead of saving and restoring the scan flag
    assert registry.available_blocks() == {}


def test_a_bare_import_of_the_composition_root_should_be_permitted_here() -> None:
    # given the registry, which lives at the root alongside the composition root and is not subject to a
    # layer rule of its own (docs/01 §4)
    # when it is imported
    import auto_inform  # noqa: F401

    # then nothing objects: this test exists to pin that the exemption is deliberate, so that adding a
    # root rule later is a decision rather than an accident
    assert registry.get_block("Wafer map").display_name == "Wafer map"


def test_concurrent_lookups_should_all_succeed() -> None:
    # given the registry, whose scan and tables are guarded by a re-entrant lock
    results: list[type[Block[object]]] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def look_up() -> None:
        start.wait()  # release all eight at once, to give a real collision a chance
        try:
            results.append(registry.get_block("Maintenance history"))
        except BaseException as exc:  # noqa: BLE001 - surface anything at all
            errors.append(exc)

    # when eight threads look a block up simultaneously, one of them racing the lazy scan
    threads = [threading.Thread(target=look_up) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    # then every thread got the class. The scan imports modules while holding the lock, and each
    # decorated class registers on the same thread -- so the lock has to be re-entrant or this deadlocks
    assert errors == []
    assert len(results) == 8


def test_register_block_with_something_that_is_not_a_block_should_raise_configuration_error() -> (
    None
):
    # given a class that merely has the attribute the registry keys on
    class Impostor:
        display_name = "Not a block"

    # when it is registered
    # then it is refused at the point of the mistake, rather than registering and failing later inside
    # collect(), where the cause is nowhere near by
    with pytest.raises(ConfigurationError, match="not a Block subclass"):
        registry.register_block(Impostor)  # type: ignore[arg-type]


def test_this_module_should_pass_when_run_on_its_own() -> None:
    """The guard for the isolation fixture above, which a whole-suite run cannot provide.

    Process-global tables plus a scan flag are exactly the shape that produces order-dependence, and an
    earlier version of this module restored the tables but not the flag: this file then failed three
    tests when run alone while the full suite stayed green, because ``test_blocks_*`` sorts first and had
    already populated the registry. That is what hermeticity rule 4 of docs/09 §2 forbids, and it is
    invisible from inside the suite -- hence a child process.

    ``-k not ...`` deselects this test in the child run, which would otherwise recurse for ever.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__).resolve()),
            "-q",
            "-k",
            "not test_this_module_should_pass_when_run_on_its_own",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        timeout=300,
    )

    assert completed.returncode == 0, f"{completed.stdout[-3000:]}\n{completed.stderr[-1000:]}"

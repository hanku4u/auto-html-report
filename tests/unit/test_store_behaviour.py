"""The store behaviour suite, run against every implementation.

``docs/04-events-and-stores.md`` calls for one suite run against both backends. SQLite runs here today;
stage 11 appends a PostgreSQL factory to ``STORE_FACTORIES`` and these same cases run against it, with
the module marked ``postgres`` and skipped unless ``AUTO_INFORM_TEST_POSTGRES_DSN`` is set.

**A case belongs here only if it is a property of the port.** Anything that needs to see a row's raw
columns -- payload immutability, ``last_error`` being cleared -- lives in the backend's own module
against that backend's storage, because the port deliberately exposes no read-by-id method. That gap is
real and worth knowing: an operator diagnoses a retired event with SQL, not through this interface.

Retirement is exercised at ``max_attempts=2`` so the boundary is two calls rather than three, and the
"exactly at the limit" case is unambiguous.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from auto_inform.application.ports import EventStore
from auto_inform.domain.errors import StoreError
from auto_inform.infrastructure.stores.sqlite_store import SqliteEventStore
from tests.support.builders import make_event
from tests.support.clocks import DEFAULT_START

StoreFactory = Callable[[Path], EventStore]


def _sqlite_store(tmp_path: Path) -> EventStore:
    return SqliteEventStore(tmp_path / "events.sqlite3", max_attempts=2)


#: One entry per implementation. The cases below may not assume SQL, a file path, or that the store
#: under test is the default one.
STORE_FACTORIES = [pytest.param(_sqlite_store, id="sqlite")]


@pytest.fixture(params=STORE_FACTORIES)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventStore]:
    """An open store, closed even when the test fails."""
    factory: StoreFactory = request.param
    with factory(tmp_path) as opened:
        yield opened


# --- save: insert-only and idempotent ----------------------------------------


def test_save_with_new_events_should_report_them_as_new(store: EventStore) -> None:
    # given an empty store
    # when two events are saved
    result = store.save([make_event("a"), make_event("b")])

    # then both are new and neither is a duplicate
    assert (result.new, result.duplicates) == (2, 0)


def test_save_of_the_same_events_again_should_report_them_as_duplicates(store: EventStore) -> None:
    # given a store that already holds the events
    events = [make_event("a"), make_event("b")]
    store.save(events)

    # when the same rolling window is discovered again
    result = store.save(events)

    # then the second batch is entirely duplicates and nothing is doubled
    assert (result.new, result.duplicates) == (0, 2)
    assert len(store.pending()) == 2


def test_save_of_a_processed_event_should_not_reset_it_to_new(store: EventStore) -> None:
    # given an event that has already produced a report
    event = make_event("a")
    store.save([event])
    store.mark_processed(event, output_uri="file:///reports/a.html")

    # when discovery sees it again
    result = store.save([event])

    # then the row is untouched: it stays processed and never re-enters the queue. Re-discovery of a
    # rolling window must not undo delivered work (docs/04 §2.1)
    assert result.duplicates == 1
    assert store.pending() == []


# --- pending: the work queue --------------------------------------------------


def test_pending_should_be_oldest_first(store: EventStore) -> None:
    # given events saved in an order that is not their discovery order
    store.save(
        [
            make_event("newest", discovered_at=DEFAULT_START + timedelta(minutes=2)),
            make_event("oldest", discovered_at=DEFAULT_START),
            make_event("middle", discovered_at=DEFAULT_START + timedelta(minutes=1)),
        ]
    )

    # when the queue is read
    # then it is chronological, because starvation is a head-of-line problem
    assert [event.event_id for event in store.pending()] == ["oldest", "middle", "newest"]


def test_pending_should_exclude_processed_events(store: EventStore) -> None:
    # given one processed and one pending event
    done, waiting = make_event("done"), make_event("waiting")
    store.save([done, waiting])
    store.mark_processed(done, output_uri="file:///reports/done.html")

    # when the queue is read
    # then only the pending event is returned
    assert [event.event_id for event in store.pending()] == ["waiting"]


def test_pending_should_exclude_retired_events(store: EventStore) -> None:
    # given an event that has exhausted its attempts
    broken = make_event("broken")
    store.save([broken])
    store.mark_failed(broken, reason="first")
    store.mark_failed(broken, reason="second")

    # when the queue is read
    # then the retired event is gone: it must not starve later events for ever (ADR-0019)
    assert store.pending() == []


def test_pending_should_honour_a_limit(store: EventStore) -> None:
    # given three pending events
    store.save([make_event(letter) for letter in "abc"])

    # when the queue is capped
    # then the cap is respected, oldest first
    assert [event.event_id for event in store.pending(limit=2)] == ["a", "b"]


def test_pending_should_filter_by_event_type(store: EventStore) -> None:
    # given events of two types
    store.save(
        [
            make_event("wafer", event_type="WAFER_FAILURE"),
            make_event("maintenance", event_type="MAINTENANCE"),
        ]
    )

    # when the queue is filtered
    # then only the requested type is returned
    assert [event.event_id for event in store.pending(event_type="MAINTENANCE")] == ["maintenance"]


def test_pending_on_an_empty_store_should_return_nothing(store: EventStore) -> None:
    assert store.pending() == []


# --- lifecycle ----------------------------------------------------------------


def test_mark_failed_below_the_limit_should_leave_the_event_pending(store: EventStore) -> None:
    # given a store whose limit is two attempts
    event = make_event("a")
    store.save([event])

    # when the first attempt fails
    store.mark_failed(event, reason="temporary")

    # then the event is still queued for the next run
    assert [pending.event_id for pending in store.pending()] == ["a"]


def test_mark_failed_at_the_limit_should_retire_the_event_and_free_the_queue(
    store: EventStore,
) -> None:
    # given two events, the older one permanently broken
    broken = make_event("broken", discovered_at=DEFAULT_START)
    healthy = make_event("healthy", discovered_at=DEFAULT_START + timedelta(minutes=1))
    store.save([broken, healthy])

    # when the older one fails its first attempt, then its second (the limit)
    store.mark_failed(broken, reason="first")
    assert [event.event_id for event in store.pending(limit=1)] == ["broken"]
    store.mark_failed(broken, reason="second")

    # then it is retired, and the event behind it is no longer starved (acceptance criteria 17 and 18)
    assert [event.event_id for event in store.pending(limit=1)] == ["healthy"]


def test_mark_processed_should_take_the_event_out_of_the_queue(store: EventStore) -> None:
    # given a delivered event
    event = make_event("a")
    store.save([event])

    # when it is marked processed
    store.mark_processed(event, output_uri="file:///reports/a.html")

    # then it is not offered again
    assert store.pending() == []


# --- lifecycle misuse and teardown --------------------------------------------


def test_mark_processed_with_an_unknown_event_should_raise_store_error(store: EventStore) -> None:
    # given a store that never saw this event
    # when it is marked processed
    # then the store says so instead of silently doing nothing
    with pytest.raises(StoreError, match="not a NEW event"):
        store.mark_processed(make_event("ghost"), output_uri="file:///reports/ghost.html")


def test_mark_failed_with_an_unknown_event_should_raise_store_error(store: EventStore) -> None:
    with pytest.raises(StoreError, match="not a NEW event"):
        store.mark_failed(make_event("ghost"), reason="nothing to fail")


def test_save_with_no_events_should_report_nothing(store: EventStore) -> None:
    # given an empty batch, which a discoverer returning nothing produces
    result = store.save([])

    # then it is a successful no-op rather than an error
    assert (result.new, result.duplicates) == (0, 0)


def test_close_should_be_idempotent(store: EventStore) -> None:
    # given an open store
    # when it is closed twice
    store.close()
    store.close()

    # then the second close is a no-op rather than an error: a job must be able to tear down after an
    # exception without having to reason about whether it already closed


def test_save_of_a_key_owned_by_another_source_should_raise_store_error(store: EventStore) -> None:
    # given an event persisted by one feed
    store.save([make_event("a", source="feed-one")])

    # when a different feed presents the same (event_type, event_id)
    with pytest.raises(StoreError, match="already stored by source"):
        store.save([make_event("a", source="feed-two")])

    # then the collision is loud, and the first row is intact: insert-only storage would otherwise let
    # one source silently suppress another's event for ever, with only a duplicate count as a trace
    assert [event.event_id for event in store.pending()] == ["a"]


def test_mark_failed_on_an_already_retired_event_should_raise_store_error(
    store: EventStore,
) -> None:
    # given an event that has exhausted its two attempts
    event = make_event("a")
    store.save([event])
    store.mark_failed(event, reason="one")
    store.mark_failed(event, reason="two")

    # when a third failure is recorded
    # then it is refused rather than silently ignored: the lifecycle has already left NEW
    with pytest.raises(StoreError, match="not a NEW event"):
        store.mark_failed(event, reason="three")

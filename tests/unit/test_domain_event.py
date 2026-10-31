"""The Event value object and its lifecycle states."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from auto_html_report.domain.event import Event, EventStatus

DISCOVERED_AT = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


def _event(
    *,
    event_type: str = "MAINTENANCE_OVERDUE",
    event_id: str = "MAINTENANCE_OVERDUE:T-104",
    payload: Mapping[str, Any] | None = None,
    discovered_at: datetime = DISCOVERED_AT,
    source: str = "maintenance-api",
) -> Event:
    return Event(
        event_type=event_type,
        event_id=event_id,
        payload={"tool_id": "T-104", "days_overdue": 12} if payload is None else payload,
        discovered_at=discovered_at,
        source=source,
    )


def test_event_status_members_should_be_the_three_lifecycle_states() -> None:
    # given the lifecycle an event moves through in the store
    # when the enum is inspected
    # then NEW, PROCESSED and FAILED are the only states, and FAILED means retired
    assert [status.value for status in EventStatus] == ["NEW", "PROCESSED", "FAILED"]


def test_event_should_be_frozen() -> None:
    # given a discovered event
    event = _event()

    # when a field is reassigned
    # then it is refused: the event is shared between two independently scheduled jobs, and one of
    # them only ever reads it
    with pytest.raises(FrozenInstanceError):
        event.event_id = "different"  # type: ignore[misc]


def test_event_should_carry_no_lifecycle_state_of_its_own() -> None:
    # given an event that has just been discovered
    event = _event()

    # when its attributes are inspected
    # then none of the store's lifecycle fields are present: status, attempts and the output URI
    # belong to the store, because a frozen record that mutates its own status would be a lie
    for store_owned_field in ("status", "attempts", "last_error", "output_uri", "processed_at"):
        assert not hasattr(event, store_owned_field)


def test_event_should_not_copy_its_payload() -> None:
    # given a payload mapping
    payload = {"tool_id": "T-104"}

    # when an event is built around it
    # then the same object is carried, not a copy -- the framework never mutates a payload, and
    # copying would silently break a caller who relies on identity
    assert _event(payload=payload).payload is payload


def test_events_differing_only_in_type_should_be_distinguishable() -> None:
    # given two events that share an id but not a type
    first = _event(event_type="A", event_id="same")
    second = _event(event_type="B", event_id="same")

    # when their identity is considered
    # then the pair of type and id separates them, which is why the store keys on both and a
    # re-discovery of the same event is seen as a duplicate rather than a new row
    assert (first.event_type, first.event_id) != (second.event_type, second.event_id)


def test_event_discovered_at_should_keep_its_timezone() -> None:
    # given an event discovered at a known instant
    # then the timestamp is timezone-aware, because the store is oldest-first and two jobs in
    # different zones must agree on which event is oldest
    assert _event().discovered_at.tzinfo is not None

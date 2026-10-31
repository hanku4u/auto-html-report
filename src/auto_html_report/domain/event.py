"""The Event value object and its lifecycle states.

An :class:`Event` is a pure value with **no lifecycle state**. Lifecycle lives in the event store
and is updated through explicit calls, because a frozen record that mutates its own status is a
lie -- and because two independently scheduled jobs share the record, one of which only reads.
See ``docs/04-events-and-stores.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class EventStatus(StrEnum):
    """Where an event is in its lifecycle. ``FAILED`` means retired: never returned again."""

    NEW = "NEW"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Event:
    """Something discovered at an external source that may deserve a report.

    ``payload`` is typed read-only, and the framework never mutates it -- but that is a convention,
    not a guarantee: a ``dict`` handed in here stays mutable to whoever else holds a reference. Pass
    a ``MappingProxyType`` or a frozen structure if you need the contents to be immutable too.
    """

    event_type: str
    event_id: str
    payload: Mapping[str, Any]
    discovered_at: datetime
    source: str

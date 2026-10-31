"""The manufacturing family: the factory is registered, and it binds the event into its blocks.

``docs/03-report-api.md`` §3 is the convention under test: the factory reads the event, passes its fields
into constructors, and returns a definition. The event is not stuffed into a context, so a block that
needs ``tool_id`` declares it and cannot depend on ambient state.
"""

from __future__ import annotations

import pytest

from auto_html_report.domain.event import Event
from auto_html_report.registry import get_report_factory
from auto_html_report.reports.manufacturing import WAFER_FAILURE_EVENT, wafer_failure_report
from tests.support.clocks import DEFAULT_START
from tests.support.contexts import make_report_context


def _event(**payload: object) -> Event:
    fields: dict[str, object] = {
        "lot_id": "L1001",
        "tool_id": "T-104",
        "summary": "Below yield threshold",
    }
    fields.update(payload)
    return Event(
        event_type=WAFER_FAILURE_EVENT,
        event_id="WAFER_FAILURE:L1001",
        payload=fields,
        discovered_at=DEFAULT_START,
        source="yield-api",
    )


def test_importing_the_family_should_register_the_factory() -> None:
    # given the module is imported (this test module imports it)
    # when the registry is asked for the event type
    # then it is the manufacturing factory, so a job script gets it by importing the family
    assert get_report_factory(WAFER_FAILURE_EVENT) is wafer_failure_report


def test_the_factory_should_bind_the_event_into_the_definition_and_its_blocks() -> None:
    # given a failed-wafer event
    # when its report is built
    definition = wafer_failure_report(_event(), make_report_context())

    # then the event supplies the identity, the framing and the block parameters
    assert definition.title == "Failed wafer — WAFER_FAILURE:L1001"
    assert definition.subtitle == "Below yield threshold"
    assert definition.event_id == "WAFER_FAILURE:L1001"
    assert definition.metadata == {"Lot": "L1001", "Tool": "T-104"}
    assert [block.name for block in definition.blocks] == [
        "Wafer map — L1001",
        "Maintenance history — T-104",
    ]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"lot_id": "L1001"}, "tool_id"),
        ({"lot_id": "L1001", "tool_id": None}, "tool_id"),
        ({"lot_id": ["L1001"], "tool_id": "T-104"}, "lot_id"),
        ({"lot_id": "", "tool_id": "T-104"}, "lot_id"),
    ],
    ids=["absent", "null", "wrong-type", "empty"],
)
def test_a_missing_or_wrongly_typed_payload_field_should_raise_value_error(
    payload: dict[str, object], field: str
) -> None:
    # given a payload whose field is absent, null, wrongly typed or empty -- a malformed upstream response
    event = Event(
        event_type=WAFER_FAILURE_EVENT,
        event_id="WAFER_FAILURE:L1001",
        payload=payload,
        discovered_at=DEFAULT_START,
        source="yield-api",
    )

    # when the report is built
    # then it raises naming the field: the processing job records this against the event and retries. A
    # null silently becoming the lot id "None" would produce a report that looks real and is about nothing
    with pytest.raises(ValueError, match=field):
        wafer_failure_report(event, make_report_context())


def test_a_non_string_summary_should_be_ignored_rather_than_rendered() -> None:
    # given a payload whose optional summary is not a string
    event = Event(
        event_type=WAFER_FAILURE_EVENT,
        event_id="WAFER_FAILURE:L1001",
        payload={"lot_id": "L1001", "tool_id": "T-104", "summary": None},
        discovered_at=DEFAULT_START,
        source="yield-api",
    )

    # when the report is built
    # then it simply has no subtitle, instead of the text "None"
    assert wafer_failure_report(event, make_report_context()).subtitle is None

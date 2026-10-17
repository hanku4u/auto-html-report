"""The maintenance-history block, exercised against a fake client.

This is the shipped version of the worked example in ``docs/02-block-authoring.md`` §7, so the tests here
are also a check that the example is implementable as written: constructor parameters rather than ambient
state, a timeout on every call, a self-classified transient failure, and escaping on the way out.
"""

from __future__ import annotations

import pytest

from auto_inform.blocks.maintenance_history import MaintenanceHistoryBlock, MaintenanceRecord
from auto_inform.domain.errors import TransientBlockError
from auto_inform.infrastructure.settings import Settings
from auto_inform.registry import get_block
from tests.support.contexts import make_render_context, make_report_context
from tests.support.http import FakeHttpClient

PATH = "/maintenance/T-104"

FIXTURE = {
    "status": "ok",
    "records": [
        {"performed_at": "2026-10-15", "engineer": "A. Rivera", "summary": "Replaced thermocouple"},
        {"performed_at": "2026-10-02", "engineer": "J. Okafor", "summary": "Cleaned chamber"},
    ],
}


def _http(payload: object = FIXTURE) -> FakeHttpClient:
    return FakeHttpClient({PATH: payload})


# --- collect ------------------------------------------------------------------


def test_collect_should_return_the_records_the_client_supplied() -> None:
    # given a tool with maintenance history at the API
    context = make_report_context(http=_http())

    # when the block collects
    # then it returns typed records rather than the raw payload, so render() never touches JSON
    data = MaintenanceHistoryBlock(tool_id="T-104").collect(context)

    assert data == [
        MaintenanceRecord("2026-10-15", "A. Rivera", "Replaced thermocouple"),
        MaintenanceRecord("2026-10-02", "J. Okafor", "Cleaned chamber"),
    ]


def test_collect_should_ask_for_the_tools_history_with_the_requested_window() -> None:
    # given a block configured for the last week
    http = _http()

    # when it collects
    MaintenanceHistoryBlock(tool_id="T-104", days=7).collect(make_report_context(http=http))

    # then it asks for that tool over that window -- the parameters are the block's, not ambient state
    assert http.calls[0].path == PATH
    assert http.calls[0].params == {"days": 7}


def test_collect_should_keep_the_http_timeout_within_the_blocks_own_budget() -> None:
    # given the shipped defaults, where the global HTTP timeout (60 s) is *longer* than this block's own
    # framework budget (30 s)
    http = _http()
    block = MaintenanceHistoryBlock(tool_id="T-104")

    # when it collects
    block.collect(make_report_context(http=http))

    # then the call carried the block's budget, not the global default. Passing the default was the
    # original defect here: a call allowed to run past the budget is abandoned mid-flight, because the
    # framework cannot kill a thread -- it merely stops waiting (docs/02-block-authoring.md §6.2)
    assert http.calls[0].timeout == 30.0
    assert http.calls[0].timeout == block.timeout_seconds


def test_collect_should_never_pass_a_timeout_longer_than_its_budget() -> None:
    # given a deliberately generous global default, far longer than the block declares
    http = _http()
    block = MaintenanceHistoryBlock(tool_id="T-104")
    settings = Settings(block_timeout_seconds=600.0)

    # when it collects
    block.collect(make_report_context(http=http, settings=settings))

    # then the transport timeout stays inside the budget whatever the global setting says. This is the
    # property rather than one example of it, and it is the check that would have caught the mismatch
    budget = block.timeout_seconds or settings.block_timeout_seconds
    assert http.calls[0].timeout <= budget


def test_collect_with_a_degraded_service_should_raise_a_transient_error() -> None:
    # given an API that answers, but says it cannot answer properly
    http = _http({"status": "degraded", "records": []})

    # when the block collects
    # then it raises the framework's transient error, so the retry policy can act on a condition only
    # the block could have recognised -- the transport saw a perfectly good 200
    with pytest.raises(TransientBlockError, match="degraded"):
        MaintenanceHistoryBlock(tool_id="T-104").collect(make_report_context(http=http))


def test_collect_with_a_record_missing_a_field_should_raise_rather_than_return_a_partial_record() -> (
    None
):
    # given a payload whose row does not match the documented shape
    http = _http({"status": "ok", "records": [{"performed_at": "2026-10-15"}]})

    # when the block collects
    # then it raises rather than filling in blanks. Collection classifies this as permanent, which is
    # right: a retry will not repair a payload shape
    with pytest.raises(TypeError):
        MaintenanceHistoryBlock(tool_id="T-104").collect(make_report_context(http=http))


# --- render -------------------------------------------------------------------


def test_render_should_return_a_table_with_the_documented_headers() -> None:
    # given two records
    records = [
        MaintenanceRecord("2026-10-15", "A. Rivera", "Replaced thermocouple"),
        MaintenanceRecord("2026-10-02", "J. Okafor", "Cleaned chamber"),
    ]

    # when the block renders
    html = MaintenanceHistoryBlock(tool_id="T-104").render(records, make_render_context())

    # then it returns the *inside* of a Data cell: a table, with headers as documented, and no wrapper
    assert html.startswith('<table class="ai-data-table">')
    assert '<th scope="col">Date</th>' in html
    assert '<th scope="col">Engineer</th>' in html
    assert '<th scope="col">Summary</th>' in html
    assert "Replaced thermocouple" in html
    assert "<td" not in html.split("<tbody>")[0]


def test_render_should_escape_every_value_that_came_from_the_api() -> None:
    # given API values that look like markup -- a hostile string in a vendor name is exactly the case
    # the trust boundary exists for, since this HTML is inserted with |safe
    records = [
        MaintenanceRecord("<script>alert(1)</script>", '"quoted"', "<img src=x onerror=alert(1)>")
    ]

    # when the block renders
    html = MaintenanceHistoryBlock(tool_id="T-104").render(records, make_render_context())

    # then nothing became a tag or an attribute
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html
    assert "&lt;img" in html


def test_render_without_records_should_return_the_documented_empty_state() -> None:
    # given a window with no maintenance in it
    # when the block renders
    # then it says so in words, rather than emitting a table with headers and no rows. A block decides
    # its own empty state; html_table would happily give it the header-only version
    html = MaintenanceHistoryBlock(tool_id="T-104").render([], make_render_context())

    assert html == '<p class="ai-empty">No maintenance recorded in this window.</p>'


# --- the block's identity and registration ------------------------------------


def test_the_block_should_be_registered_under_its_display_name() -> None:
    # given the built-in block package
    # when the registry is asked for this block by name
    # then the scan found it, so a predefined report can name it rather than importing the class
    assert get_block("Maintenance history") is MaintenanceHistoryBlock


def test_name_should_combine_the_display_name_with_the_tool() -> None:
    # given a block for one tool
    # when its name is read
    # then it carries the tool id: a report routinely shows maintenance for several tools, and two rows
    # called "Maintenance history" would be indistinguishable
    assert MaintenanceHistoryBlock(tool_id="T-104").name == "Maintenance history — T-104"


def test_an_explicit_name_should_override_the_default() -> None:
    # given a caller who wants a different Item text
    # then it wins, which is the reason `name` is a property rather than a class attribute
    assert (
        MaintenanceHistoryBlock(tool_id="T-104", name="Line 4 maintenance").name
        == "Line 4 maintenance"
    )


def test_the_block_should_declare_the_documented_timeout() -> None:
    # given the class-level override the worked example sets
    # then it is the documented value: the framework prefers a block's own budget over the global default
    assert MaintenanceHistoryBlock.timeout_seconds == 30.0

"""Exit codes: the precedence table of ``docs/05`` §5, as a pure function over synthetic runs.

One test per rank, plus ties: the table's whole point is that the most severe condition wins, and a
"highest code wins" implementation would get three of these cases backwards. No store, no clock, no
writer -- the function takes a finished run and ranks what the runner recorded.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from auto_html_report.application.discovery import DiscoveryOutcome, DiscoveryRun
from auto_html_report.exit_codes import ExitCode, exit_code_for
from auto_html_report.processing import EventProcessingOutcome, ProcessingRun
from tests.support.builders import make_event, make_outcome
from tests.support.clocks import DEFAULT_START


def _processing_run(*error_types: str | None) -> ProcessingRun:
    outcomes = tuple(
        EventProcessingOutcome(
            event=make_event(f"e{index}"),
            report_title="Report",
            rows=(make_outcome("Row"),),
            output_uri=None,
            error_type=error_type,
        )
        for index, error_type in enumerate(error_types)
    )
    return ProcessingRun(
        run_id="run",
        outcomes=outcomes,
        started_at=DEFAULT_START,
        finished_at=DEFAULT_START,
    )


def _discovery_run(*error_types: str | None) -> DiscoveryRun:
    outcomes = tuple(
        DiscoveryOutcome(
            discoverer=f"Source{index}",
            event_type="WAFER_FAILURE",
            discovered=0,
            new=0,
            duplicates=0,
            duration_seconds=0.0,
            error_type=error_type,
        )
        for index, error_type in enumerate(error_types)
    )
    return DiscoveryRun(
        run_id="run",
        outcomes=outcomes,
        started_at=DEFAULT_START,
        finished_at=DEFAULT_START,
    )


def test_the_codes_should_have_the_documented_numbers() -> None:
    # given the vocabulary a CI script branches on
    # then the values are the contract, not just the names: changing one silently breaks every script
    assert (ExitCode.SUCCESS, ExitCode.PARTIAL_FAILURE) == (0, 1)
    assert (ExitCode.CONFIGURATION_ERROR, ExitCode.STORE_ERROR, ExitCode.OUTPUT_ERROR) == (2, 3, 4)


def test_a_run_with_no_outcomes_should_succeed() -> None:
    assert exit_code_for(_processing_run()) is ExitCode.SUCCESS
    assert exit_code_for(_discovery_run()) is ExitCode.SUCCESS


def test_a_run_where_everything_succeeded_should_succeed() -> None:
    assert exit_code_for(_processing_run(None, None, None)) is ExitCode.SUCCESS


def test_a_partial_failure_should_rank_last() -> None:
    # given an event that failed for a reason with no higher-ranked code
    assert exit_code_for(_processing_run(None, "KeyError")) is ExitCode.PARTIAL_FAILURE


def test_an_output_error_should_outrank_a_partial_failure() -> None:
    # a report that could not be written is more severe than one event's data problem
    assert exit_code_for(_processing_run("KeyError", "OutputError")) is ExitCode.OUTPUT_ERROR


def test_a_store_error_should_outrank_an_output_error() -> None:
    assert exit_code_for(_processing_run("OutputError", "StoreError")) is ExitCode.STORE_ERROR


def test_a_configuration_error_should_outrank_everything() -> None:
    # given a run containing every failure kind
    run = _processing_run("KeyError", "OutputError", "StoreError", "ConfigurationError")

    # then the deployment problem wins, because the others may all be consequences of it
    assert exit_code_for(run) is ExitCode.CONFIGURATION_ERROR


def test_a_block_failure_that_failed_the_event_should_be_a_partial_failure() -> None:
    # the runner records "BlockError" when AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR marks the event failed, so the
    # exit code follows the event's outcome rather than re-reading the report
    assert exit_code_for(_processing_run("BlockError")) is ExitCode.PARTIAL_FAILURE


@pytest.mark.parametrize(
    ("error_types", "expected"),
    [
        ((), ExitCode.SUCCESS),
        (("PermanentBlockError",), ExitCode.PARTIAL_FAILURE),
        (("ConfigurationError", "PermanentBlockError"), ExitCode.CONFIGURATION_ERROR),
    ],
    ids=["empty", "source-failed", "contract-violation"],
)
def test_discovery_runs_should_rank_the_same_way(
    error_types: Sequence[str], expected: ExitCode
) -> None:
    # given a discovery run with the named failures
    # when it is mapped to an exit code
    # then the same precedence applies: a contract violation is a deployment problem, not a red run
    assert exit_code_for(_discovery_run(*error_types)) is expected

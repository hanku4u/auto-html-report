"""The context factories: fresh identity, wired dependencies, and an honest S3 gap."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from auto_html_report import Block, BlockStatus, ReportDefinition, generate_report
from auto_html_report.application.context import DiscoveryContext, RenderContext, ReportContext
from auto_html_report.domain.errors import ConfigurationError
from auto_html_report.infrastructure.clock import SystemClock
from auto_html_report.infrastructure.factories import (
    build_output_writer,
    new_discovery_context,
    new_report_context,
)
from auto_html_report.infrastructure.settings import Settings, load_settings
from auto_html_report.infrastructure.writers.local_writer import LocalDirectoryWriter
from auto_html_report.infrastructure.writers.s3_writer import S3Writer
from auto_html_report.testing import StubHttpClient
from tests.support.clocks import FakeClock


def test_new_report_context_should_wire_the_settings_and_a_fresh_run_id() -> None:
    # given the same settings twice
    settings = load_settings({})

    # when two contexts are built
    first = new_report_context(settings)
    second = new_report_context(settings)

    # then each carries the settings and its own run id, which is what makes two reports traceable to
    # different log lines even in one long-lived process
    assert first.settings is settings
    assert first.run.run_id != second.run.run_id
    assert isinstance(first.clock, SystemClock)
    assert first.logger.name == "auto_html_report"


@pytest.mark.parametrize("factory", [new_report_context, new_discovery_context])
@pytest.mark.parametrize("method", ["get_json", "get_bytes"])
def test_a_context_without_an_http_client_should_fail_on_use_and_name_the_argument(
    factory: Callable[[Settings], ReportContext | DiscoveryContext], method: str
) -> None:
    # given a context built with no client, which is legitimate for a report with no HTTP blocks
    http = factory(load_settings({})).http

    # when a block calls it anyway
    with pytest.raises(ConfigurationError, match=r"no HTTP client configured.*http="):
        getattr(http, method)("/x", timeout=1.0)

    # then the error names the fix: a production job that forgot http= must not be told about fixtures


def test_generate_report_without_an_http_client_should_isolate_the_block_that_needs_one() -> None:
    # given a report mixing a block with no I/O and a block that calls the unconfigured client
    class LocalBlock(Block[str]):
        display_name = "Local"

        def collect(self, context: ReportContext) -> str:
            return "local data"

        def render(self, data: str, context: RenderContext) -> str:
            return data

    class RemoteBlock(Block[str]):
        display_name = "Remote"

        def collect(self, context: ReportContext) -> str:
            return str(context.http.get_json("/x", timeout=1.0))

        def render(self, data: str, context: RenderContext) -> str:
            return data

    definition = ReportDefinition(title="Mixed", blocks=[LocalBlock(), RemoteBlock()])

    # when it is generated without a client
    result = generate_report(definition, context=new_report_context(load_settings({})))

    # then the local block succeeds, and only the remote block fails, with the fix in its error cell
    local, remote = result.report.rows
    assert local.outcome.status is BlockStatus.OK
    assert remote.outcome.status is BlockStatus.FAILED
    assert "no HTTP client configured" in result.html


def test_factories_should_accept_injected_dependencies() -> None:
    # given a caller that wants canned data and a deterministic clock -- a test, or a demo job
    http = StubHttpClient({"/x": {}})
    clock = SystemClock()
    logger = logging.getLogger("custom")

    # when the context is built
    context = new_report_context(load_settings({}), http=http, clock=clock, logger=logger)

    # then the injected objects are used, so these factories are not a one-way door into production wiring
    assert context.http is http
    assert context.clock is clock
    assert context.logger is logger


def test_new_discovery_context_should_have_its_own_run_id() -> None:
    # given a report context and a discovery context from the same settings
    settings = Settings()

    # then their run ids differ, because the two jobs are separate runs in the log
    assert new_report_context(settings).run.run_id != new_discovery_context(settings).run.run_id


def test_build_output_writer_should_select_the_local_writer() -> None:
    # given the default output kind
    settings = Settings()

    # when the writer is selected
    # then it writes into the configured directory
    assert isinstance(build_output_writer(settings), LocalDirectoryWriter)


def test_build_output_writer_with_s3_should_configure_the_s3_writer_from_settings() -> None:
    # given an S3 destination with a prefix and encryption, and an injected client so no network is used
    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def put_object(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return {}

    client = _Client()
    settings = load_settings(
        {
            "AUTO_HTML_REPORT_OUTPUT_KIND": "s3",
            "AUTO_HTML_REPORT_OUTPUT_S3_BUCKET": "reports-bucket",
            "AUTO_HTML_REPORT_OUTPUT_S3_PREFIX": "reports",
            "AUTO_HTML_REPORT_OUTPUT_S3_SSE": "AES256",
        }
    )

    # when the writer is selected and used
    writer = build_output_writer(settings, s3_client=client)
    location = writer.write(filename="r.html", content="x")

    # then the settings reached the writer: prefix, encryption and bucket are all applied, and there is no
    # silent fallback to local output
    assert isinstance(writer, S3Writer)
    assert client.calls[0]["Key"] == "reports/r.html"
    assert client.calls[0]["ServerSideEncryption"] == "AES256"
    assert location.uri == "s3://reports-bucket/reports/r.html"


def test_build_output_writer_with_s3_and_no_bucket_should_fail_at_start_up() -> None:
    # given a directly constructed Settings that names S3 but no bucket (the loader would have refused it)
    settings = Settings(output_kind="s3")

    # when the writer is selected
    # then it fails before the job runs rather than at the first report
    with pytest.raises(ConfigurationError, match="OUTPUT_S3_BUCKET"):
        build_output_writer(settings)


def test_context_factories_should_take_started_at_from_the_injected_clock() -> None:
    # given a clock with a known instant
    start = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)

    # when each context factory builds a run with it injected
    for factory in (new_report_context, new_discovery_context):
        context = factory(Settings(), clock=FakeClock(start=start))

        # then the run is stamped by that clock, not by wall time. This is the control for the fix that
        # closed a hidden second read of time: without it, a refactor could reintroduce the read while
        # every other factory assertion still passes
        assert context.run.started_at == start


def test_build_output_writer_with_an_unknown_kind_should_raise_configuration_error() -> None:
    # given a directly constructed Settings with a kind the loader cannot produce (the type is a Literal,
    # so the ignore is the test stepping outside it deliberately)
    settings = Settings(output_kind="gcs")  # type: ignore[arg-type]

    # when the writer is selected
    # then it is refused rather than silently reinterpreted as S3
    with pytest.raises(ConfigurationError, match="unknown output kind"):
        build_output_writer(settings)

"""The context factories: fresh identity, wired dependencies, and an honest S3 gap."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from auto_inform.domain.errors import ConfigurationError
from auto_inform.infrastructure.clock import SystemClock
from auto_inform.infrastructure.factories import (
    build_output_writer,
    new_discovery_context,
    new_report_context,
)
from auto_inform.infrastructure.http_stub import StubHttpClient
from auto_inform.infrastructure.settings import Settings, load_settings
from auto_inform.infrastructure.writers.local_writer import LocalDirectoryWriter
from auto_inform.infrastructure.writers.s3_writer import S3Writer
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
    assert isinstance(first.http, StubHttpClient)
    assert first.logger.name == "auto_inform"


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
            "AUTO_INFORM_OUTPUT_KIND": "s3",
            "AUTO_INFORM_OUTPUT_S3_BUCKET": "reports-bucket",
            "AUTO_INFORM_OUTPUT_S3_PREFIX": "reports",
            "AUTO_INFORM_OUTPUT_S3_SSE": "AES256",
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

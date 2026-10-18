"""The context factories: fresh identity, wired dependencies, and an honest S3 gap."""

from __future__ import annotations

import logging

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


def test_build_output_writer_should_select_the_local_writer(tmp_path: object) -> None:
    # given the default output kind
    settings = Settings()

    # when the writer is selected
    # then it writes into the configured directory
    assert isinstance(build_output_writer(settings), LocalDirectoryWriter)


def test_build_output_writer_with_s3_should_fail_clearly() -> None:
    # given an S3 destination, which this stage cannot serve
    settings = load_settings(
        {"AUTO_INFORM_OUTPUT_KIND": "s3", "AUTO_INFORM_OUTPUT_S3_BUCKET": "bucket"}
    )

    # when the writer is selected
    # then it fails at start-up rather than silently writing locally: a job that believes it is uploading
    # and is not is worse than one that will not start
    with pytest.raises(ConfigurationError, match="stage 11"):
        build_output_writer(settings)

"""The example scripts, executed rather than merely imported.

``docs/11`` stage 10's done-when is that a reader can follow the README to a generated report. The
strongest version of that claim is a test that runs the same code the README shows: the quick start
writes a document, and the two jobs take events from discovery to two written reports.

The scripts call ``configure_logging``, which installs a handler on the root logger, so the fixture below
saves and restores it -- the same isolation rule ``test_logging_setup`` uses.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from auto_html_report import ExitCode, Settings
from auto_html_report.testing import StubHttpClient
from examples import discover_wafers, process_events, quick_start
from examples.demo_api import ROUTES, demo_http
from examples.plumbing_check import _check_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _settings(tmp_path: Path) -> Settings:
    return Settings(output_dir=tmp_path, sqlite_path=tmp_path / "events.sqlite3")


def test_the_quick_start_should_write_a_report_with_one_row(tmp_path: Path) -> None:
    # given the code the README shows
    # when it runs against a scratch directory
    assert quick_start.main(tmp_path) is ExitCode.SUCCESS

    # then one self-contained document exists, with the block's row in it
    (report,) = tmp_path.glob("*.html")
    html = report.read_text(encoding="utf-8")
    assert "Quick start" in html
    assert "Deployment" in html
    assert "<table" in html and "Check" in html


def test_the_two_jobs_should_take_events_from_discovery_to_written_reports(
    tmp_path: Path,
) -> None:
    # given the demo API and the two scripts
    settings = _settings(tmp_path)

    # when discovery runs and processing follows it
    assert discover_wafers.main(settings) is ExitCode.SUCCESS
    assert process_events.main(settings) is ExitCode.SUCCESS

    # then there is one report per failed lot, with a chart and the maintenance table in it
    reports = sorted(tmp_path.glob("*.html"))
    assert len(reports) == 2
    html = reports[0].read_text(encoding="utf-8")
    assert "WAFER_FAILURE:L1001" in html
    assert "data:image/png;base64," in html  # the wafer map rendered as a data URI
    assert "A. Rivera" in html  # the maintenance history table


def test_running_both_jobs_again_should_not_duplicate_or_rebuild_anything(
    tmp_path: Path,
) -> None:
    # given a pipeline that has already delivered both reports
    settings = _settings(tmp_path)
    discover_wafers.main(settings)
    process_events.main(settings)
    before = sorted(path.name for path in tmp_path.glob("*.html"))

    # when both jobs run again
    assert discover_wafers.main(settings) is ExitCode.SUCCESS
    assert process_events.main(settings) is ExitCode.SUCCESS

    # then discovery reports duplicates and processing finds nothing pending: the store is what makes
    # the schedule safe to re-run
    assert sorted(path.name for path in tmp_path.glob("*.html")) == before


def _run_documented(arguments: Sequence[str], output_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run a command exactly as the README shows it, from the repository root.

    A subprocess, not an import: the failure this guards against is an import path that only works under
    pytest's ``pythonpath`` -- which is precisely how the documented command broke once already.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "AUTO_HTML_REPORT_OUTPUT_DIR": str(output_dir),
        "AUTO_HTML_REPORT_SQLITE_PATH": str(output_dir / "events.sqlite3"),
        "AUTO_HTML_REPORT_LOG_FORMAT": "console",
    }
    return subprocess.run(
        [sys.executable, *arguments],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_documented_quick_start_command_should_run_as_written(tmp_path: Path) -> None:
    # given the quick-start command from the README, verbatim
    completed = _run_documented(["examples/quick_start.py", str(tmp_path)], tmp_path)

    # then it succeeds and writes its report
    assert completed.returncode == 0, completed.stderr
    assert len(list(tmp_path.glob("*.html"))) == 1


def test_the_documented_job_commands_should_run_as_written(tmp_path: Path) -> None:
    # given the two module commands from the README, verbatim (including ``-m``, which is what makes the
    # shared demo fixtures importable)
    for module in ("examples.discover_wafers", "examples.process_events"):
        completed = _run_documented(["-m", module], tmp_path)

        # then each succeeds
        assert completed.returncode == 0, completed.stderr

    # and the pair produced one report per failed lot
    assert len(list(tmp_path.glob("*.html"))) == 2


def test_the_adapter_in_the_examples_should_be_the_one_the_documentation_prints() -> None:
    # given the recipe in docs/02 §5.2, which readers copy, and the file CI actually runs
    document = (REPO_ROOT / "docs" / "02-block-authoring.md").read_text(encoding="utf-8")
    section = document[document.index("### 5.2 Writing an adapter") :]
    recipe = section[
        section.index("```python\n") + len("```python\n") : section.index("```\n\nPass it")
    ]

    # when they are compared
    source = (REPO_ROOT / "examples" / "http_client.py").read_text(encoding="utf-8")

    # then the code is identical, so what CI exercises is what the documentation promises
    assert recipe in source


def test_the_plumbing_check_should_pass_with_every_route_answered(tmp_path: Path) -> None:
    # given the pipeline half of the check, on the stub rather than sockets (docs/09 §2 rule 1)
    settings = Settings(output_dir=tmp_path, sqlite_path=tmp_path / "events.sqlite3")

    # when it runs against a client that answers every route the jobs use
    problems = _check_pipeline(settings, demo_http())

    # then it has nothing to report
    assert problems == []


def test_the_plumbing_check_should_report_a_block_that_failed(tmp_path: Path) -> None:
    # given a client missing the route one block of each report needs
    routes = {path: body for path, body in ROUTES.items() if not path.startswith("/maintenance/")}
    settings = Settings(
        output_dir=tmp_path,
        sqlite_path=tmp_path / "events.sqlite3",
        block_retries=0,
        retry_base_delay_seconds=0.0,
    )

    # when the check runs
    problems = _check_pipeline(settings, StubHttpClient(routes))

    # then it objects, naming the block: a check that cannot fail would prove nothing (docs/09 §2 rule 7)
    assert any("Maintenance history" in problem and "FAILED" in problem for problem in problems)


def test_the_plumbing_check_should_report_a_discovery_that_failed(tmp_path: Path) -> None:
    # given a client with no routes at all
    settings = Settings(
        output_dir=tmp_path,
        sqlite_path=tmp_path / "events.sqlite3",
        block_retries=0,
        retry_base_delay_seconds=0.0,
    )

    # when the check runs
    problems = _check_pipeline(settings, StubHttpClient())

    # then it reports both the failed discovery and the reports that never appeared
    assert any(problem.startswith("discovery exited") for problem in problems)
    assert any(problem.startswith("expected 2 reports") for problem in problems)

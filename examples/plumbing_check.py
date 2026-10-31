"""Run the whole pipeline over real HTTP against ``examples.local_api``, and check what happened.

    uv run python -m examples.plumbing_check [output_dir]

It exits 0 only if everything behaved as specified, and leaves two kinds of report to open in a browser: the
two wafer reports from the discovery and processing jobs, and a "Plumbing check" report whose rows show
retries, error cells and a timeout. The default output directory is ``./reports/plumbing-check``.

This runs in CI as its own step rather than under pytest, because the suite's first rule is no network access
(``docs/09`` §2), and this opens real sockets on purpose.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import auto_html_report.reports.manufacturing  # noqa: F401 - importing registers the factory
from auto_html_report import (
    Block,
    BlockStatus,
    ExitCode,
    HttpClient,
    LocalDirectoryWriter,
    PermanentBlockError,
    RenderContext,
    ReportContext,
    ReportDefinition,
    Settings,
    configure_logging,
    exit_code_for,
    generate_report,
    html_table,
    new_discovery_context,
    new_report_context,
    run_discovery,
    run_processing,
)
from examples.demo_api import LOT_IDS, TOOL_ID
from examples.discover_wafers import WaferFailureDiscoverer
from examples.http_client import UrllibHttpClient
from examples.local_api import FLAKY_FAILURES, LocalApi, local_api
from examples.store import open_store

DEFAULT_OUTPUT_DIR = Path("reports/plumbing-check")

#: The retries a failing route gets, on top of its first attempt.
_RETRIES = FLAKY_FAILURES


class _RouteBlock(Block[object]):
    """Fetches one route, and shows the response."""

    def __init__(self, path: str) -> None:
        super().__init__(name=f"GET {path}")
        self.path = path

    def collect(self, context: ReportContext) -> object:
        timeout = self.timeout_seconds or context.settings.block_timeout_seconds
        return context.http.get_json(self.path, timeout=timeout)

    def render(self, data: object, context: RenderContext) -> str:
        return html_table(headers=["Response"], rows=[(json.dumps(data),)])


class _SlowRouteBlock(_RouteBlock):
    """A short budget and no retries, so the check spends half a second on the timeout."""

    timeout_seconds = 0.5
    max_retries = 0


@dataclass(frozen=True)
class _Expected:
    block: _RouteBlock
    status: BlockStatus
    #: Both what the outcome reports and what the server saw, which is what proves a retry was real.
    attempts: int


def _expected_rows() -> list[_Expected]:
    return [
        _Expected(_RouteBlock(f"/maintenance/{TOOL_ID}"), BlockStatus.OK, 1),
        _Expected(_RouteBlock("/test/flaky"), BlockStatus.OK, FLAKY_FAILURES + 1),
        _Expected(_RouteBlock("/test/error"), BlockStatus.FAILED, _RETRIES + 1),
        _Expected(_RouteBlock("/test/missing"), BlockStatus.FAILED, 1),
        _Expected(_RouteBlock("/test/malformed"), BlockStatus.FAILED, 1),
        _Expected(_SlowRouteBlock("/test/slow"), BlockStatus.TIMED_OUT, 1),
    ]


def _check_client(api: LocalApi) -> list[str]:
    """The adapter on its own: a good request, a refused token, and a server that is not there."""
    problems: list[str] = []
    client = UrllibHttpClient(api.url, token=api.token)

    if client.get_json("/yield/failures", timeout=5.0) != {"lots": list(LOT_IDS)}:
        problems.append("/yield/failures returned the wrong data")

    try:
        UrllibHttpClient(api.url).get_json("/yield/failures", timeout=5.0)
        problems.append("a request with no token was accepted")
    except PermanentBlockError as error:
        if "401" not in str(error):
            problems.append(f"a request with no token failed the wrong way: {error}")

    try:
        UrllibHttpClient("http://127.0.0.1:1").get_json("/x", timeout=5.0)
        problems.append("a request to a closed port succeeded")
    except ConnectionError:
        pass

    return problems


def _check_pipeline(settings: Settings, client: HttpClient) -> list[str]:
    """Discovery, then processing, then processing again, which must find nothing left to do."""
    problems: list[str] = []
    with open_store(settings) as store:
        discovery = run_discovery(
            [WaferFailureDiscoverer()],
            store=store,
            context=new_discovery_context(settings, http=client),
        )
        if exit_code_for(discovery) is not ExitCode.SUCCESS:
            problems.append(f"discovery exited {exit_code_for(discovery)!r}: {discovery.outcomes}")

        writer = LocalDirectoryWriter(settings.output_dir)
        context = new_report_context(settings, http=client)
        processing = run_processing(store=store, context=context, writer=writer)
        if exit_code_for(processing) is not ExitCode.SUCCESS:
            problems.append(f"processing exited {exit_code_for(processing)!r}")
        if len(processing.outcomes) != len(LOT_IDS):
            problems.append(f"expected {len(LOT_IDS)} reports, got {len(processing.outcomes)}")
        for outcome in processing.outcomes:
            print(f"  wrote {outcome.output_uri}")
            for row in outcome.rows:
                if row.status is not BlockStatus.OK:
                    problems.append(f"{outcome.report_title}: {row.name} was {row.status.value}")

        rerun = run_processing(
            store=store, context=new_report_context(settings, http=client), writer=writer
        )
        if rerun.outcomes:
            problems.append(f"re-running processing redid {len(rerun.outcomes)} events")
    return problems


def _check_failure_handling(
    api: LocalApi, settings: Settings, client: UrllibHttpClient
) -> list[str]:
    """One report whose rows succeed, retry, fail and time out, and must still be written."""
    problems: list[str] = []
    expected = _expected_rows()
    result = generate_report(
        ReportDefinition(title="Plumbing check", blocks=[row.block for row in expected]),
        context=new_report_context(settings, http=client),
        writer=LocalDirectoryWriter(settings.output_dir),
    )
    assert result.location is not None
    print(f"  wrote {result.location.uri}")

    for want, row in zip(expected, result.report.rows, strict=True):
        got = row.outcome
        served = api.hits(want.block.path)
        print(f"  {want.block.path:<22} {got.status.value:<10} attempts={got.attempts}")
        if got.status is not want.status:
            problems.append(
                f"{want.block.path}: wanted {want.status.value}, got {got.status.value}"
            )
        if got.attempts != want.attempts or served < want.attempts:
            problems.append(
                f"{want.block.path}: wanted {want.attempts} attempts, "
                f"the outcome says {got.attempts} and the server saw {served}"
            )
    return problems


def main(output_dir: Path = DEFAULT_OUTPUT_DIR) -> int:
    # Errors are the point here, so the log would only bury the summary.
    configure_logging("CRITICAL", "console")
    with tempfile.TemporaryDirectory() as scratch, local_api() as api:
        # A fresh store each run, because a second run against an old one would rightly find no new events.
        settings = Settings(
            output_dir=output_dir,
            sqlite_path=Path(scratch) / "events.sqlite3",
            block_retries=_RETRIES,
            retry_base_delay_seconds=0.05,
            log_level="CRITICAL",
            log_format="console",
        )
        client = UrllibHttpClient(api.url, token=api.token)
        print(f"local API at {api.url}")

        print("the client on its own")
        problems = _check_client(api)
        print("discovery and processing")
        problems += _check_pipeline(settings, client)
        print("failure handling")
        problems += _check_failure_handling(api, settings, client)

    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    print("plumbing check failed" if problems else "plumbing check passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_DIR))

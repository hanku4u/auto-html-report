"""Run everything over real HTTP against ``examples.local_api``, and check what happened.

    uv run python -m examples.plumbing_check [output_dir]

It exits 0 only if everything behaved as designed. This is a CI step rather than a test, because it opens real
sockets.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from auto_html_report import Block, FetchError, build_report, html_table
from examples.demo_api import TOOL_ID
from examples.http_client import UrllibHttpClient
from examples.local_api import FLAKY_FAILURES, LocalApi, local_api

_RETRY_DELAY = 0.01


def get_route(path: str, http: Any, timeout: float = 5.0) -> Any:
    return http.get_json(path, timeout=timeout)


class RouteBlock(Block):
    gather = get_route

    def render(self, data: Any) -> str:
        return html_table(["Response"], [(data,)])


class BrokenRender(RouteBlock):
    def render(self, data: Any) -> str:
        raise ValueError("this block cannot render")


def _route(path: str, *, title: str | None = None, block: type[RouteBlock] = RouteBlock) -> Block:
    return block(title=title or f"GET {path}", path=path)


def _check(api: LocalApi, output_dir: Path) -> list[str]:
    problems: list[str] = []
    http = UrllibHttpClient(api.url, token=api.token)
    context = {"http": http}
    maintenance = f"/maintenance/{TOOL_ID}"

    print("sharing, retrying and contained render failures")
    report = build_report(
        "Plumbing check",
        [
            _route(maintenance),
            _route(maintenance, title="The same call again"),
            _route("/test/flaky"),
            _route(maintenance, title="Broken render", block=BrokenRender),
        ],
        context,
        retry_delay=_RETRY_DELAY,
    )
    print(f"  wrote {report.save(output_dir)}")
    if api.hits(maintenance) != 1:
        problems.append(f"{maintenance} got {api.hits(maintenance)} calls, wanted 1")
    if api.hits("/test/flaky") != FLAKY_FAILURES + 1:
        problems.append(f"/test/flaky got {api.hits('/test/flaky')} calls")
    if [row.title for row in report.failed_rows] != ["Broken render"]:
        problems.append(f"wrong failed rows: {report.failed_rows}")

    print("failing fast")
    for path, retries, calls in (
        ("/test/error", 3, 4),  # retried, then fatal
        ("/test/missing", 3, 1),  # a 404: fatal at once
        ("/test/malformed", 3, 1),  # not JSON: fatal at once
        ("/test/slow", 0, 1),  # the client's own timeout fires
    ):
        block = RouteBlock(title=path, path=path, timeout=0.3)
        try:
            build_report("Should fail", [block], context, retries=retries, retry_delay=_RETRY_DELAY)
            problems.append(f"{path} produced a report")
        except FetchError as error:
            print(f"  {path}: {error}")
        if api.hits(path) != calls:
            problems.append(f"{path} got {api.hits(path)} calls, wanted {calls}")
    return problems


def main(output_dir: Path = Path("reports/plumbing-check")) -> int:
    with local_api() as api:
        problems = _check(api, output_dir)
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    print("plumbing check failed" if problems else "plumbing check passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*(Path(arg) for arg in sys.argv[1:2])))

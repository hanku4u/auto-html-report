# auto_html_report

`auto_html_report` turns a set of small Python classes into **one self-contained HTML report** — a single
file you can attach to a ticket, publish from a CI job, or open on a machine with no network.

The report is one table with three columns:

| Item | Data | Check |
| --- | --- | --- |
| Deployment | Python 3.11.9, linux | |
| Maintenance history — T-104 | a table of recent maintenance events | |
| Wafer map — L1001 | an embedded PNG chart | |

Each row is contributed by a **block**: a class that knows how to gather one piece of information
(`collect()`) and how to render it as HTML (`render()`). The Check column is deliberately empty: it is a
placeholder for a human reviewer's sign-off. The document is static — nothing writes to that column,
and v1 ships no JavaScript — so you print it or annotate it outside the browser.

The framework does the parts that are tedious to get right in every job script:

- blocks run **concurrently**, each with its own timeout and retry budget;
- a block that fails becomes an **error cell**; the rest of the report still renders;
- row order is deterministic, matching the order you listed the blocks in;
- the finished document is genuinely **self-contained** — inline CSS, charts embedded as base64 PNG
  data URIs, no JavaScript, no external requests.

It is a library, not a service: there is no CLI. Report definitions and job scripts are ordinary
Python, and the scheduled runner is your existing CI system.

## Quick start

You need Python 3.11 or newer. In a checkout, [uv](https://docs.astral.sh/uv/) provisions it for you.

```bash
git clone https://github.com/hanku4u/auto-html-report.git
cd auto-html-report
uv sync --frozen
uv run python examples/quick_start.py /tmp/report-demo
```

That writes `quick-start__<timestamp>.html` into `/tmp/report-demo`. Open it in a browser: one row,
filled by a block that reads nothing but the local interpreter, so the example runs anywhere.

Here is the whole program ([`examples/quick_start.py`](examples/quick_start.py)):

```python
import sys
from pathlib import Path

from auto_html_report import (
    Block,
    ExitCode,
    LocalDirectoryWriter,
    RenderContext,
    ReportContext,
    ReportDefinition,
    Settings,
    generate_report,
    html_table,
    new_report_context,
)


class DeploymentBlock(Block[list[tuple[str, str]]]):
    """One row of facts about this machine's Python, gathered without I/O."""

    display_name = "Deployment"

    def collect(self, context: ReportContext) -> list[tuple[str, str]]:
        return [
            ("Python", sys.version.split()[0]),
            ("Platform", sys.platform),
        ]

    def render(self, data: list[tuple[str, str]], context: RenderContext) -> str:
        # html_table() escapes every cell, which is the one thing a block author must not forget.
        return html_table(headers=["Key", "Value"], rows=data)


def main(output_dir: Path) -> ExitCode:
    settings = Settings(output_dir=output_dir)
    definition = ReportDefinition(title="Quick start", blocks=[DeploymentBlock()])
    result = generate_report(
        definition,
        context=new_report_context(settings),
        writer=LocalDirectoryWriter(settings.output_dir),
    )
    # A writer was supplied, so the result carries where the document landed.
    assert result.location is not None
    print(f"wrote {result.location.uri}")
    return ExitCode.SUCCESS


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("reports")))
```

Swap that `collect()` for an HTTP call — or any other I/O — and the rest of the program is unchanged.
That is the entire extension model.

## Installation

The distribution is named `auto-html-report`; the import name is `auto_html_report`. It is not published to
PyPI — install it straight from git, pinned to a tag or commit SHA.

```bash
# into a uv-managed project
uv add "auto_html_report @ git+https://github.com/hanku4u/auto-html-report.git@v0.1.0"

# plain pip works too
pip install "auto_html_report @ git+https://github.com/hanku4u/auto-html-report.git@v0.1.0"
```

Install commands always spell the **underscore** form, including in the extras position, because pip is
inconsistent about the hyphen:

```bash
uv add "auto_html_report[s3] @ git+https://github.com/hanku4u/auto-html-report.git@v0.1.0"
pip install "auto_html_report[postgres,s3] @ git+https://github.com/hanku4u/auto-html-report.git@v0.1.0"
```

**Pin a tag or SHA; do not install from a branch.** uv and pip both cache git sources, so an unpinned
`@main` can serve a stale package at the worst possible moment. Pinning is the fix, not a workaround.
When you genuinely need to chase a moving ref, force a refresh: `uv add --refresh ...` or
`pip install --force-reinstall --no-cache-dir ...`.

`v0.1.0` is the intended first tag. If it does not exist yet in your clone, pin a commit SHA instead.

The extras select the optional backends: `s3` for uploading reports instead of writing them locally,
`postgres` for a shared event store instead of SQLite. Both are optional — the defaults need neither,
and `import auto_html_report` works without them. An S3 writer bounds its upload timeouts itself, because the
processing job does not wait for it with a deadline.

## Writing a block

A block subclasses `auto_html_report.Block[DataT]` and implements two methods:

- **`collect(self, context: ReportContext) -> DataT`** — the I/O phase. It may call an API, read a
  database, or compute something local.
- **`render(self, data: DataT, context: RenderContext) -> str`** — the pure phase. It returns the
  **inner HTML of the Data cell**, not a `<td>` and not a document.

Here is the built-in maintenance block
([`src/auto_html_report/blocks/maintenance_history.py`](src/auto_html_report/blocks/maintenance_history.py)),
trimmed to the essentials:

```python
from dataclasses import dataclass

from auto_html_report import (
    Block,
    RenderContext,
    ReportContext,
    TransientBlockError,
    html_table,
)


@dataclass(frozen=True, slots=True)
class MaintenanceRecord:
    performed_at: str
    engineer: str
    summary: str


class MaintenanceHistoryBlock(Block[list[MaintenanceRecord]]):
    display_name = "Maintenance history"
    timeout_seconds = 30.0

    def __init__(self, *, tool_id: str, days: int = 30, name: str | None = None) -> None:
        super().__init__(name=name or f"Maintenance history — {tool_id}")
        self._tool_id = tool_id
        self._days = days

    def collect(self, context: ReportContext) -> list[MaintenanceRecord]:
        payload = context.http.get_json(
            f"/maintenance/{self._tool_id}",
            params={"days": self._days},
            # The block's effective budget: its own override, else the setting.
            timeout=self.timeout_seconds or context.settings.block_timeout_seconds,
        )
        if payload.get("status") == "degraded":
            raise TransientBlockError("maintenance service degraded; retry later")
        return [MaintenanceRecord(**row) for row in payload["records"]]

    def render(self, data: list[MaintenanceRecord], context: RenderContext) -> str:
        if not data:
            return '<p class="ai-empty">No maintenance recorded in this window.</p>'
        return html_table(
            headers=["Date", "Engineer", "Summary"],
            rows=[(record.performed_at, record.engineer, record.summary) for record in data],
        )
```

The rules that matter:

- **Every HTTP call goes through `context.http` and passes `timeout=`.** The framework cannot interrupt
  a running thread, so a call without a timeout can outlive its own budget. Pass the block's effective
  budget, as above — not the global default.
- **Classify failures.** Raise `TransientBlockError` for retryable conditions (a 503, a degraded
  service) and `PermanentBlockError` for the rest (a 404, a payload shape a retry will not improve).
  Anything else is treated as permanent by default.
- **`render()` is pure** — no I/O, no clock, no randomness, no logging. It gets the value its own
  `collect()` returned and must not raise.
- **Escape anything from outside the process.** Use `escape()` for text,
  `html_table(headers=..., rows=...)` for tables (it escapes every cell), and `figure_to_data_uri()`
  for matplotlib charts. Skipping this is an HTML injection bug, not a cosmetic one.
- **Set `display_name`**, or pass `name=...` when instantiating, to fill the Item column. Names must be
  unique within a report; `ReportDefinition` rejects duplicates when you build it.

Blocks run concurrently, with a per-attempt timeout, retries with backoff, and failure isolation: a
block that times out or raises produces an error cell and the other rows still render. One flaky API
does not cost you the report.

Read [`docs/02-block-authoring.md`](docs/02-block-authoring.md) for the full contract, the HTTP error
mapping, and the timeout accounting rules.

## Reports that scale with discovered data

Generating one report is a function call. Generating *a report per failed lot, every hour* is a job —
and that is what the two-phase model is for. The two phases share only an event store (SQLite in v1),
so either can fail, be re-run, or run on a different schedule without affecting the other.

1. **Discovery** — one or more `Discoverer` subclasses yield `Event`s from an API; `run_discovery()`
   persists them. Saving is insert-only and idempotent, so re-running over a rolling API window
   reports duplicates instead of redoing work.
2. **Processing** — `run_processing()` takes pending events, looks up the report factory registered for
   each event type, builds and writes one report per event, and records where it landed. An event is
   marked delivered only after the write succeeds.

Both return a run object. `exit_code_for(run)` maps it to an exit code your CI job can branch on:

| Code | Meaning |
| --- | --- |
| `0` | Everything succeeded. |
| `1` | Partial failure — at least one event or block failed, but reports were produced. |
| `2` | Configuration error, e.g. a report factory for an event type was never imported. |
| `3` | The event store failed. |
| `4` | A report could not be written. |

The two demo scripts show the whole pipeline against canned fixtures:

```bash
uv run python -m examples.discover_wafers    # persists two WAFER_FAILURE events
uv run python -m examples.process_events     # writes one report per event
```

The second script's first import is load-bearing:

```python
import auto_html_report.reports.manufacturing  # importing registers the factory
```

Importing the module registers the `WAFER_FAILURE` factory. Forget it and every event of that type is
skipped and the run exits `2` — loudly, which is the point.

Running both writes two reports, each with a wafer-map chart and a maintenance-history table, into
`./reports` by default.

`-m` rather than the script path: running the file directly puts `examples/` on `sys.path` instead of
the repository root, and the shared fixtures in `examples.demo_api` would not import.

`examples/demo_api.py` supplies the fixtures through `auto_html_report.testing.StubHttpClient`, which
answers every `get_json()` call from a fixture table. The package ships **no real HTTP client**: you write
a small adapter for the `HttpClient` protocol and pass it as `http=`, and a block that needs a client when
none was given fails in its own cell with an error saying so.
[`docs/02-block-authoring.md`](docs/02-block-authoring.md) §5.2 has a recipe.

See [`docs/05-discovery-and-processing.md`](docs/05-discovery-and-processing.md) for the job model,
throughput settings, and worked job scripts.

## Configuration

Configuration comes from environment variables only — parsed once into a frozen `Settings` object, with
no config file and no flags. These are the ones a job normally sets:

| Variable | Default | What it does |
| --- | --- | --- |
| `AUTO_HTML_REPORT_OUTPUT_KIND` | `local` | `local` or `s3`; `s3` also needs `AUTO_HTML_REPORT_OUTPUT_S3_BUCKET`. |
| `AUTO_HTML_REPORT_OUTPUT_DIR` | `./reports` | Directory the local writer writes into. |
| `AUTO_HTML_REPORT_EVENT_STORE` | `sqlite` | `sqlite` or `postgres`; `postgres` also needs `AUTO_HTML_REPORT_POSTGRES_DSN`. |
| `AUTO_HTML_REPORT_POSTGRES_DSN` | — | The PostgreSQL DSN, password included; `sslmode=require` (or `verify-full`) for a shared database. |
| `AUTO_HTML_REPORT_OUTPUT_S3_BUCKET` | — | Bucket for `AUTO_HTML_REPORT_OUTPUT_KIND=s3`; required for S3. |
| `AUTO_HTML_REPORT_OUTPUT_S3_PREFIX` | `""` | Key prefix under the bucket. |
| `AUTO_HTML_REPORT_OUTPUT_S3_TIMEOUT_SECONDS` | `30` | S3 connect/read timeout (capped at 300). |
| `AUTO_HTML_REPORT_OUTPUT_S3_SSE` | — | `AES256`, `aws:kms` or `aws:kms:dsse`; omitted means the bucket default. |
| `AUTO_HTML_REPORT_SQLITE_PATH` | `<output_dir>/events.sqlite3` | Event store location. |
| `AUTO_HTML_REPORT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `AUTO_HTML_REPORT_LOG_FORMAT` | `json` | `json` for log shippers, `console` for humans. |
| `AUTO_HTML_REPORT_MAX_WORKERS` | `8` | In-flight blocks per report (capped at 64). |
| `AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS` | `1` | Reports built in parallel per run (capped at 32); raises in-flight HTTP calls, so size it against the API. |
| `AUTO_HTML_REPORT_BLOCK_TIMEOUT_SECONDS` | `60` | Per-attempt budget for one block. |
| `AUTO_HTML_REPORT_BLOCK_RETRIES` | `2` | Retries after the first attempt (capped at 10). |
| `AUTO_HTML_REPORT_PROCESSING_LIMIT` | `500` | Events one processing run takes from the queue (capped at 10,000). |
| `AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR` | `true` | Treat a failed block as a failed event, so the run exits non-zero. |

Invalid values are reported together in one `ConfigurationError`, not one typo per CI run.

The full table — every variable, its `Settings` attribute, type and bound — is
[`docs/07-configuration-and-logging.md`](docs/07-configuration-and-logging.md) §1.1.

## Development

A checkout uses [uv](https://docs.astral.sh/uv/) for the interpreter and dependencies:

```bash
uv sync --frozen --all-extras   # install the pinned environment, extras included
uv lock --check                 # lockfile agrees with pyproject.toml
uv run ruff check               # lint
uv run ruff format --check      # formatting
uv run mypy                     # types, strict
uv run pytest                   # tests
```

`.github/workflows/ci.yml` runs exactly these commands in this order, so a green local run means a
green CI run — no service to start, and no network beyond fetching the pinned packages.

One more check catches what a source-tree test cannot:

```bash
./scripts/check_install.sh
```

It builds the wheel, installs it into a scratch environment, and runs the quick start from a directory
that is not the repository. A template missing from the wheel or a package directory the build backend
did not infer fails there instead of at first report. CI also installs the package from a git URL pinned
to the commit — the same form as the Installation section, using `file://` for the checked-out tree so a
private repository needs no token.

Runtime dependencies are declared as ranges in `pyproject.toml`, and everything is pinned in the committed
`uv.lock` for this repository's own environments. Change a dependency, run `uv lock` in the same commit, or
`uv lock --check` will fail.

## Status

v1 is complete: stages 1–11 of [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md).
Local directory output and SQLite are the defaults; the `s3` and `postgres` extras are implemented and
optional. There is no built-in API client: blocks and discoverers are written against the `HttpClient`
protocol, and you supply the implementation.

## Where to look

| If you want to… | Read |
| --- | --- |
| Understand what the tool does and does not do | [`docs/01-overview.md`](docs/01-overview.md) |
| Write a block | [`docs/02-block-authoring.md`](docs/02-block-authoring.md) |
| Run discovery and processing jobs | [`docs/05-discovery-and-processing.md`](docs/05-discovery-and-processing.md) |
| Configure it | [`docs/07-configuration-and-logging.md`](docs/07-configuration-and-logging.md) |
| Know why a design decision was made | [`docs/10-decision-log.md`](docs/10-decision-log.md) |
| See what is built and what is next | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) |

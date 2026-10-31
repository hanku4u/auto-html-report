# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`auto_html_report` is a Python library (no CLI, no service) that turns `Block` classes into one self-contained
HTML report: inline CSS, charts as base64 PNG data URIs, no JavaScript, no external requests. It is licensed
Apache-2.0 and published on PyPI (`docs/12-publishing.md`). The distribution is `auto-html-report`; the import name is `auto_html_report`. It ships no real HTTP
client: consumers implement the `HttpClient` port and pass `http=`; `auto_html_report.testing.StubHttpClient`
answers from fixtures for tests and demos.

## Commands

The environment is managed by uv. CI (`.github/workflows/ci.yml`) runs these in this order on Python 3.11 to
3.14, plus a run at the dependency floors, the wheel install check and a history secret scan:

```bash
uv sync --frozen --all-extras   # --all-extras is required: tests exercise psycopg and boto3 paths
uv lock --check
uv run ruff check
uv run ruff format --check
uv run mypy                     # strict; covers src, tests and examples
uv run pytest
```

```bash
uv run pytest tests/unit/test_exit_codes.py                        # one file
uv run pytest tests/unit/test_exit_codes.py::test_the_codes_should_have_the_documented_numbers  # one test
uv run pytest -k golden                                            # by name

./scripts/check_install.sh      # build the wheel, install it, run the quick start outside the repo

# PostgreSQL store suite: self-skips (about 32 tests) unless a DSN is set
AUTO_HTML_REPORT_TEST_POSTGRES_DSN=postgresql://test:test@localhost:5432/auto_html_report uv run pytest

# Regenerate the golden document, only after reviewing the diff
uv run python -c "from tests.support.golden import write_golden; write_golden()"

# Examples: use -m for the two job scripts, or the examples.demo_api import fails
uv run python examples/quick_start.py /tmp/report-demo
uv run python -m examples.discover_wafers
uv run python -m examples.process_events

# Run everything over real HTTP against a local test API (examples/local_api.py); also a CI step
uv run python -m examples.plumbing_check
```

Releases are tag-driven; `docs/12-publishing.md` §6.1 has the steps. Don't rename `release.yml` or the `PyPI` and
`TestPyPI` environments without updating the trusted publishers on both indexes.

Runtime dependencies are ranges with tested floors, dev tools are exact pins, and `uv.lock` pins everything for
this repo. Run `uv lock` in the same commit as any change.

## The spec drives the code

`docs/` is the normative specification; `docs/README.md` is its index and conventions. No feature or
nontrivial refactor is implemented before the relevant document has been reviewed and moved to `active`.
Module docstrings cite the sections and ADRs they implement, so follow those references.

- Read `docs/10-decision-log.md` (ADR-0001 onward) before proposing a design change. It records alternatives
  already rejected.
- A decision goes in the log as the *why*; the affected document records the *what*. A new dependency needs a
  **Dependency changes** section with its range or pin.
- `docs/` is checked by tests (`tests/unit/test_doc_*.py`). They fail on a broken link or anchor, a name a
  document imports that `auto_html_report` does not export, a `docs/08` `pyproject.toml` snippet that differs from
  the real file, a stale ADR range or next-document number, missing frontmatter, or a document over 250 lines
  with no Dev plan. A change to `pyproject.toml`, a public name or an ADR can therefore fail a docs test.

## Architecture

Dependencies point inward, and `tests/unit/test_layering.py` enforces this by reading every module's imports
(`docs/01-overview.md` §4 has the full rules):

- `domain/` is pure. Its only upward references are the two context names, under `TYPE_CHECKING`.
- `application/` uses an **allowlist**: its own layer, `domain/`, the `Settings` value type, and `_naming` /
  `_version`. It gets no concrete adapter and no `rendering`; adapters arrive by injection through the ports in
  `application/ports.py`.
- `blocks/` and `reports/` never import `application/` or `infrastructure/`; they receive what they need
  through `ReportContext` and constructor parameters. `rendering/` never imports those two layers either.
- `auto_html_report/__init__.py` is the composition root and the public surface. No layer may import it back.
- `generate.py`, `processing.py`, `exit_codes.py` and `registry.py` sit at the package root rather than in
  `application/` because composing needs the renderer and registry (ADR-0028, ADR-0030). This is deliberate.

A report is built as follows: `ReportDefinition` (pure data) → `generate_report` → `collect_blocks` runs each
`Block.collect()` concurrently through `application/execution.py` → `Block.render()` → `Report` →
`render_report` (Jinja2 shell in `rendering/templates/`) → `OutputWriter`.

Scheduled work is two decoupled jobs that share only the `EventStore`:

- `run_discovery`: `Discoverer`s yield `Event`s. Saving is insert-only and idempotent.
- `run_processing`: takes pending events, looks up the factory registered for each `event_type`, and builds and
  writes one report per event. Both jobs return a run object that `exit_code_for` maps to a CI exit code (0-4).

## Invariants that are easy to break

- **Timeouts and retries live only in `application/execution.py`** (ADR-0029), shared by blocks and discoverers.
  Budgets and backoff use the injected `Clock`; the last-resort hang guard deliberately uses real time (ADR-0027).
- **`EventStore` is thread-affine.** Processing workers only collect, render and write. Every store call happens
  on the calling thread in the commit loop, at any `AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS` setting.
- **An event is marked `PROCESSED` only after the writer returns.** A failed write leaves it `NEW`. A missing
  factory also leaves it `NEW`, counts no attempt, and exits 2. Failures are classified with `isinstance`.
- **`render()` is pure and must not raise**: no I/O, clock, randomness or logging. It returns the inner HTML of
  the Data cell. Anything from outside the process goes through `escape()` or `html_table()`.
- **Every `context.http` call passes `timeout=`**, set to the block's effective budget, because a running thread
  cannot be interrupted.
- **Importing a factory module is what registers it.** Job scripts must import e.g.
  `auto_html_report.reports.manufacturing`. Built-in blocks are found by a lazy recursive scan of `blocks/`.
- **Imports stay lazy.** `import auto_html_report` must not load matplotlib/pyplot (pinned by a subprocess test), and
  `psycopg`/`boto3` load only when an adapter is constructed, which also validates its configuration (ADR-0031).
- **Matplotlib is process-global.** Use `figure_to_data_uri()` and `figure_lock`; ADR-0025 records a wrong-chart
  bug from saving the current figure.
- **Configuration comes from environment variables only**, parsed once into a frozen `Settings`. A new variable
  also belongs in the table in `docs/07-configuration-and-logging.md` §1.1.

## Test conventions

`docs/09-testing-and-acceptance.md` §2 treats these as hard requirements:

- No network, no real wall-clock timing (use `FakeClock` from `tests/support/clocks.py`), no live services except
  the self-skipping PostgreSQL suite, and no dependence on test order. Doubles and builders are in `tests/support/`.
- Name tests `test_<setup>_should_<outcome>`, and split bodies into `# given / # when / # then` comments.
- Registry state is process-global, so a test that snapshots it must snapshot it fully populated.
- A test of something that depends on process-global state needs a competing object alive (a decoy figure, say).
- An audit of other code must ship a negative control that fails if the audit breaks.

All tests currently live in `tests/unit/`; the `integration/` directory shown in `docs/01` §6 does not exist.

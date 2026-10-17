---
status: proposed
description: The staged build order for v1, with a review boundary and a definition of done for each stage.
replaces: docs/SPEC.md §14
---

# 11 — Implementation plan

This is the **Dev plan** required for specifications of this size. Each stage is independently
reviewable and leaves the repository green: lint, types and tests all pass at every stage boundary.

Stages are ordered to front-load risk and to make the output contract concrete early. The two
highest-risk items — the collector's concurrency semantics and the escaping boundary — are stages 5
and 3 respectively, both before any job scaffolding exists.

## Stage 1 — Packaging skeleton

`src/` layout, `pyproject.toml` and `uv.lock` carrying the exact pins from
[08](08-packaging.md), `.python-version` pinned to `3.11`, `.gitignore`, `README.md` stub, ruff and
mypy configuration.

**Done when:** `uv sync --frozen` installs the package from a checked-out tree into a clean
environment, `uv lock --check` passes, and `uv run ruff check`, `uv run ruff format --check` and
`uv run mypy` pass on an empty package.

## Stage 2 — Domain layer

`domain/block.py`, `domain/event.py`, `domain/report.py`, `domain/errors.py`. No I/O, no
dependencies beyond the standard library. `BlockStatus`, `EventStatus`, `BlockOutcome`,
`ReportDefinition`, `ReportRow`, `Report`, `ReportResult`, the exception hierarchy.

**Done when:** unit tests cover duplicate-name rejection, empty-block-list rejection, frozen
semantics, and the `all_succeeded` / `failed_rows` agreement.

## Stage 3 — Rendering layer

The Jinja2 shell as package data loaded through `importlib.resources`, `rendering/cells.py`,
`rendering/escaping.py` (`escape`, `html_table`), and `rendering/figures.py` (`figure_to_data_uri`
with the Agg backend, the module lock and the lazy import).

**Why before the pipeline:** this fixes the output contract that everything else targets, and it is
the part most likely to change shape if left late. It is also where the trust boundary lives, so it
deserves review before blocks are written against it.

**Done when:** golden-file tests produce the document in [06 §3](06-output-html.md#3-document-skeleton),
the containment test passes with and without an anchor, and the figure smoke test leaves
`plt.get_fignums()` empty.

## Stage 4 — Registry and the first two built-in blocks

`registry.py` with lazy `import_builtin_blocks()`, plus `blocks/maintenance_history.py` and
`blocks/wafer_map.py` written against the stub HTTP client.

**Done when:** the registry rejects a duplicate `display_name`, the scan is idempotent, and both
blocks render correctly from canned fixtures.

## Stage 5 — Concurrent collection

`application/collect_blocks.py`: the thread pool, in-worker timeout accounting, retry with backoff,
failure isolation, and index-ordered assembly.

**Why here:** this is the highest-risk module in the project. It deserves its own review and its own
test file, before it is buried under the report and job layers.

**Done when:** the execution semantics in [02 §6](02-block-authoring.md#6-execution-semantics-for-one-report)
are covered — inverted completion order, retry counts and delays against a fake clock, timeout
producing `TIMED_OUT` without aborting the run, one failing block not preventing its peers, and the
sixteen-thread figure test.

## Stage 6 — Public entry points and the local writer

`application/generate_report.py`, `rendering/shell.py`, `report_filename`, and
`infrastructure/writers/local_writer.py`.

**Done when:** `generate_report(definition, context=ctx, writer=LocalDirectoryWriter(tmp))` produces
a correct file end to end, and `collect_blocks` / `render_report` are usable independently, as
[03 §2](03-report-api.md#2-the-three-public-entry-points) specifies.

**Review boundary:** at this point the library is genuinely usable for someone willing to pass
explicit blocks. Everything after this stage is the scheduled-job story.

## Stage 7 — Configuration, logging and the stub

`infrastructure/settings.py`, `logging_setup.py`, `http_stub.py`, and the five context factories from
[07 §3](07-configuration-and-logging.md#3-context-factories).

**Done when:** the env-var to attribute mapping test passes, secrets are redacted from `repr`, the
stub is thread-safe, and a `block.finish` log line parses as JSON with the documented fields.

## Stage 8 — Events and the discovery job

`infrastructure/stores/sqlite_store.py`, `application/discovery.py`, `run_discovery`.

**Done when:** the shared store behaviour suite passes against SQLite, including the retirement
boundary from ADR-0019, and a discovery run over a stub discoverer persists events idempotently.

## Stage 9 — The processing job and exit codes

`application/processing.py`, `application/exit_codes.py`, and the report-level concurrency knob from
ADR-0020.

**Done when:** every acceptance criterion in [09 §3](09-testing-and-acceptance.md#3-acceptance-criteria)
that concerns processing passes — including the two that specifically guard against regressions
here: a failed upload not being marked `PROCESSED`, and store calls never originating off the main
thread at any concurrency setting.

## Stage 10 — Report factories, example jobs, and the README

`reports/manufacturing.py` with the `WAFER_FAILURE` factory, the two example job scripts from
[05 §6](05-discovery-and-processing.md#6-example-job-scripts), and a user-facing README covering
install (with the pip git-cache gotcha), the two-phase job model, and how to write a block.

**Done when:** a reader who has never seen the code can follow the README to a generated report in a
scratch directory, and the README's install commands are the ones CI actually runs.

## Stage 11 — Optional backends

`infrastructure/stores/postgres_store.py`, `infrastructure/writers/s3_writer.py`, and the shared
behaviour suite extended to both. Both remain self-skipping in CI without their services.

**Done when:** the store suite passes against PostgreSQL when a DSN is provided, and the S3 writer
passes against an injected stub client with no network.

## Sequencing notes

- **Do not start stage 8 before stage 6 is reviewed.** The job layer is the easiest place to hide a
  misunderstanding of the report layer.
- **Stage 3 before stage 4** for a reason: the trust boundary in [06 §4](06-output-html.md#4-the-trust-boundary)
  is much cheaper to design once than to retrofit across a dozen blocks.
- **Stage 11 is genuinely optional.** A deployment using the local writer and SQLite is fully
  supported, and nothing in stages 1–10 depends on either backend.

## Acceptance criteria

The v1 acceptance criteria are not duplicated here; they live in
[09-testing-and-acceptance.md §3](09-testing-and-acceptance.md#3-acceptance-criteria) as the single
source of truth, and each stage above names the behaviours it is responsible for.

## Open questions

None.

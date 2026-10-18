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
`uv run mypy` pass on an empty package. **Completed 2026-10-17**

## Stage 2 — Domain layer

`domain/block.py`, `domain/event.py`, `domain/report.py`, `domain/errors.py`. No I/O, no
dependencies beyond the standard library. `BlockStatus`, `EventStatus`, `BlockOutcome`,
`ReportDefinition`, `ReportRow`, `Report`, `ReportResult`, the exception hierarchy.

**Amended during implementation:** the domain layer also required the *type declarations* it names
in its signatures — `application/context.py`, `application/ports.py` and the shape of
`infrastructure/settings.py` — because a signature referring to a type that does not exist cannot be
type-checked. Those modules carry their data only; the behaviour they will eventually hold
(`load_settings` and redaction in stage 7, the HTTP client in stage 7, the event store in stage 8)
still lands in its own stage. This was an ordering defect in the plan, not a scope decision.

**Done when:** unit tests cover duplicate-name rejection, empty-block-list rejection, frozen
semantics, and the `all_succeeded` / `failed_rows` agreement. **Completed 2026-10-17**

## Stage 3 — Rendering layer

The Jinja2 shell as package data loaded through `importlib.resources`, `rendering/cells.py`,
`rendering/escaping.py` (`escape`, `html_table`), and `rendering/figures.py` (`figure_to_data_uri`
with the Agg backend, the module lock and the lazy import).

**Why before the pipeline:** this fixes the output contract that everything else targets, and it is
the part most likely to change shape if left late. It is also where the trust boundary lives, so it
deserves review before blocks are written against it.

**Done when:** golden-file tests produce the document in [06 §3](06-output-html.md#3-document-skeleton),
the containment test passes with and without an anchor, and the figure smoke test leaves
`plt.get_fignums()` empty. **Completed 2026-10-17**

**Amended during implementation:** three things this description did not foresee. The stylesheet ships
as a template that the document `include`s rather than an interpolated variable, because autoescaping
sends entities into `<style>`, where they are not decoded (ADR-0024). `auto_inform/_naming.py` was
added, because the row identifier and the stage-6 filename both need the same slug rules and one
implementation stops them disagreeing. And the golden fixture uses three blocks rather than two,
because §6's error cell is part of the contract the snapshot exists to make executable.

**Post-review corrections (2026-10-17):** a review of this stage found that `figure_to_data_uri`
serialised pyplot's *current* figure rather than the one it was given, so a report with more than one
chart embedded the wrong image; that the documents claimed figure creation was serialised when only the
save could be; that `data-block` uniqueness was claimed but slugging does not provide it; and three
smaller mismatches in the error cell and the advertised imports. All are fixed, and the figure contract
is recorded in ADR-0025. The stage's tests grew from 134 to 147, including a concurrency test that
asserts each thread embeds its *own* figure rather than merely that eight payloads arrived.

## Stage 4 — Registry and the first two built-in blocks

`registry.py` with lazy `import_builtin_blocks()`, plus `blocks/maintenance_history.py` and
`blocks/wafer_map.py` written against the stub HTTP client.

**Done when:** the registry rejects a duplicate `display_name`, the scan is idempotent, and both
blocks render correctly from canned fixtures. **Completed 2026-10-17**

**Post-review corrections (2026-10-17):** a review of this stage found six things, and the substantive
ones are fixed here. The registry test module was **order-dependent**: its isolation fixture restored the
tables but not in their populated form, so the file failed three tests when run alone while the whole
suite passed — precisely the failure hermeticity rule 4 predicts, and invisible from inside the suite.
`MaintenanceHistoryBlock` declared a 30 s budget and then handed the transport the 60 s global default,
which reaches the same place as passing no timeout at all — the anti-pattern [02 §2](02-block-authoring.md#2-collect--the-io-phase)
warns about, with a documentation half that is fixed too. **Root-level modules were outside the layering
audit entirely**, so `registry.py` could have acquired an adapter undisturbed; they have a rule now. A
comment claiming a bare `import auto_inform` was flagged in every rule style was false under the denylist
rules — it is true now. And the registry's scan and tables are locked, so its thread-safety is a property
rather than an assumption. `numpy` is now a declared
direct dependency rather than a transitive one, because the wafer-map block imports it
([08 §2](08-packaging.md#2-dependency-changes)) — the spec-snippet check caught the drift of adding it to
`pyproject.toml` alone. matplotlib is imported *inside* the block's `render()` rather than at module
scope, because the registry scan imports every module in `blocks/`, so a module-level `pyplot` import
would make every process that looks a block up pay for matplotlib — the cost the lazy import exists to
avoid, now pinned by a subprocess test. And the tests use a fake client in `tests/support/http.py` rather
than the production `StubHttpClient`, which stays in stage 7: the blocks need only the port, and a test
double should not wait on a shipped fixture.

## Stage 5 — Concurrent collection

`application/collect_blocks.py`: the thread pool, in-worker timeout accounting, retry with backoff,
failure isolation, and index-ordered assembly.

**Why here:** this is the highest-risk module in the project. It deserves its own review and its own
test file, before it is buried under the report and job layers.

**Done when:** the execution semantics in [02 §6](02-block-authoring.md#6-execution-semantics-for-one-report)
are covered — inverted completion order, retry counts and delays against a fake clock, timeout
producing `TIMED_OUT` without aborting the run, one failing block not preventing its peers, and the
sixteen-thread figure test. **Completed 2026-10-18**

**Amended during implementation:** three things this description did not settle. **The error cell moved to
the renderer.** `collect_blocks` records the outcome and leaves a failed row's `html` empty;
`render_report(..., error_detail=...)` builds the cell. The layering audit made this unavoidable —
`application/` may not import `rendering/` — and it is the better split anyway: collection knows what
happened, the renderer knows how it looks, and the detail level is a presentation setting. `03 §2` and
`06 §6` say so. **The hang guard waits on real time** (ADR-0027), after a probe showed the fake-clock
polling design abandoned healthy blocks; every other timing test remains fake-driven, and exactly one
test costs real milliseconds. And **`collect_blocks` gained a `report_title`** parameter, because
`RenderContext` carries one and collection had no other way to supply it.

## Stage 6 — Public entry points and the local writer

`application/generate_report.py`, `rendering/shell.py`, `report_filename`, and
`infrastructure/writers/local_writer.py`.

**Done when:** `generate_report(definition, context=ctx, writer=LocalDirectoryWriter(tmp))` produces
a correct file end to end, and `collect_blocks` / `render_report` are usable independently, as
[03 §2](03-report-api.md#2-the-three-public-entry-points) specifies.

**Note for this stage:** `Settings.error_detail` must be passed through to
`render_report(..., error_detail=...)` here. It is wired but has no supplier yet, so
`AUTO_INFORM_ERROR_DETAIL` is silently dead until this stage connects it — and a test should prove the
setting reaches the rendered cell, not merely that the parameter exists.

**Review boundary:** at this point the library is genuinely usable for someone willing to pass
explicit blocks. Everything after this stage is the scheduled-job story. **Completed 2026-10-18**

**Amended during implementation:** three things. `generate_report` and `report_filename` live in
`auto_inform/generate.py` at the **package root**, not in `application/`, because they compose collection,
rendering and writing while `application/` may not import `rendering/` — ADR-0028; the layering audit
refused the documented placement on the first run. `ReportDefinition` gained an **`event_id`**, because
the filename's middle segment needs an event identity the definition did not carry. And wiring
`Settings.error_detail` into `render_report` exposed a **stage-5 defect**: the collector's `_summarise`
collapsed a message to its *first line*, so `detailed` could never show more than `summary`; the collector
now keeps the whole message, capped, and the renderer chooses.

**Post-review corrections (2026-10-18):** a review of this stage found seven things; five are fixed here.
`event_id` was inserted **positionally before `metadata`**, silently re-pointing the fourth argument — it
is keyword-only now and its type is validated, so the same mistake fails at construction instead of inside
`report_filename`. The header and the blocks were stamped from **two different clock reads**, disagreeing
by the whole collection duration; one instant is now captured and threaded through both. The writer's
**0600 mode** and **second-resolution filename collisions** are documented as deliberate properties rather
than left as accidents (the collision is pinned by a test). Publication also flushes the **directory
entry** after the rename, so the replacement survives a power loss and not merely a crash. The writer's
**POSIX scope** and its **sweepable temporary files** are stated in `06 §1`.

## Stage 7 — Configuration, logging and the stub

`infrastructure/settings.py`, `logging_setup.py`, `http_stub.py`, and the five context factories from
[07 §3](07-configuration-and-logging.md#3-context-factories).

**Done when:** the env-var to attribute mapping test passes, secrets are redacted from `repr`, the
stub is thread-safe, and a `block.finish` log line parses as JSON with the documented fields.
**Completed 2026-10-18**

**Post-review corrections (2026-10-18):** eight findings, all fixed. `load_settings` now **rejects
non-finite numbers** — `nan` passed every `<` comparison and silently disabled per-attempt budgets, and
`inf` made the hang guard unreachable, which is the worst possible place for an unvalidated value. It also
**validates and normalises `log_level`** at load time, so a bad level is reported with the other invalid
variables instead of surfacing later as a raw `ValueError` from `logging.setLevel`; the enum-like settings
are now matched case-insensitively, consistently with the booleans. The JSON formatter reserves its own
`ts`/`level`/`event`/`traceback` keys, so an `extra=` cannot rename the event. An injected `clock` now
stamps `RunMetadata.started_at`, closing a hidden second read of wall time. `StubNotConfiguredError`
derives from `ConfigurationError`, and `domain/errors.py` states the carve-out for plain Python contract
errors rather than leaving the hierarchy's promise overstated.

**Also closed here:** `report.start` and `report.finish` were in the logging contract but nothing emitted
them; `generate_report` logs both now. `discovery.finish` and the `event.*` events belong to stages 8–9.

**Second review (2026-10-18, after the fix commit):** the five configuration factories are now **exported
from `auto_inform`** — the module docstring already promised them and `05 §6`'s job scripts import them, so
the public surface and the documents now agree, with a test that the documented import resolves. The
injected-clock fix has its **control**: both context factories assert `started_at` equals the fake clock's
instant. `load_settings` and `configure_logging` now share **one** definition of a valid log level, having
previously disagreed about `WARN`, `NOTSET` and `info`. And a failed write logs **`report.failed`** before
`report.finish` with no URI, so the report-level event pair is complete on the one path where nothing was
written.

**Amended during implementation:** two things. The three context/writer factories live in
`infrastructure/factories.py` — the one module allowed to name concrete adapters — and accept optional
keyword overrides for `http`, `clock` and `logger`. The documented `new_report_context(settings)` call is
unchanged; the overrides make the factories testable and usable by a demo job with canned fixtures.
`build_output_writer` **raises for `AUTO_INFORM_OUTPUT_KIND=s3`** instead of falling back to the local
filesystem, because the S3 writer is stage 11's; a job that believes it is uploading and is not is worse
than one that fails at start-up. Stage 11 replaces that error with the real branch.

## Stage 8 — Events and the discovery job

`infrastructure/stores/sqlite_store.py`, `application/discovery.py`, `run_discovery`.

**Done when:** the shared store behaviour suite passes against SQLite, including the retirement
boundary from ADR-0019, and a discovery run over a stub discoverer persists events idempotently.
**Completed 2026-10-18**

**Amended during implementation:** the execution rules were **extracted** from `collect_blocks` into
`application/execution.py` rather than copied into discovery — see
[ADR-0029](10-decision-log.md#adr-0029--timeout-retry-and-hang-guard-mechanics-live-in-one-executor).
`05 §1` claimed they were parameterised "in `application/collect_blocks.py`"; that sentence now names
the new module, because a document describing an implementation that moved is exactly the drift this
project keeps catching. The extractor keeps the block success line **in the worker** through an
`on_success` callback, so `block.finish` still appears when a block finishes rather than when the
slowest sibling does.

**Store preconditions:** `mark_processed` and `mark_failed` apply only to a `NEW` row and raise
`StoreError` otherwise — recorded in `04 §2.3`, since the lifecycle table described the effect but not
the precondition. Two observations are tested against the SQLite file with a plain connection because
the port exposes no read-by-id method: a saved payload is never overwritten, and `last_error` is cleared
by a later success. That gap is deliberate in v1 (an operator uses SQL); it is the first candidate for
v0.2 if diagnosis through the API is ever wanted.

**Noted, not fixed (security review of this stage):** `pending()` without a `limit` reads the whole
queue, and `save()` is one transaction, so a very large batch holds a write long enough for the
overlapping processing job to hit SQLite's busy timeout; an operator can also set a retry count high
enough to overflow the hang-guard arithmetic; the console formatter does not escape control characters
in `extra` values, so with `AUTO_INFORM_LOG_FORMAT=console` a source that embeds a response body in an
exception can forge log lines (JSON logs are unaffected); and discoverer error text reaches the log with
no redaction boundary, which will matter once the real HTTP client can put a token in a URL. Each is
operator-sized rather than attacker-fed, or needs a policy decision (a cap, a chunk size, a bound in
`load_settings`, a redaction filter), so they are recorded rather than guessed at. The discoverer-output
ceiling *was* fixed, because that one is fed directly by external content.

**Also here:** the discovery events that were missing from the logging contract — `discovery.start`,
`retry`, `failed` and `abandoned` — mirror the block vocabulary the shared executor emits, and
`discovery.finish` gained the `discoverer` field, without which two sources producing the same event
type were indistinguishable in the log. A documented-import test now resolves every
`from auto_inform import ...` in the documentation set, with a shrinking allowlist for the two names
stage 9 lands; it found a real defect immediately (`register_report` was documented and unexported).

## Stage 9 — The processing job and exit codes

`application/processing.py`, `application/exit_codes.py`, and the report-level concurrency knob from
ADR-0020.

**Done when:** every acceptance criterion in [09 §3](09-testing-and-acceptance.md#3-acceptance-criteria)
that concerns processing passes — including the two that specifically guard against regressions
here: a failed upload not being marked `PROCESSED`, and store calls never originating off the main
thread at any concurrency setting.

**Also this stage:** the report-level failure event covers only the write path today. A crash inside
collection or rendering leaves an orphaned `report.start`, because `generate_report`'s try/finally begins
after rendering. Decide here whether the runner should own a broader `report.failed` or whether the
event-level `event.failed` covers it, and make `07 §4`'s wording follow that decision.

**Also this stage:** `FakeEventStore` lands here. `05` and `09` already describe it as the in-memory
double that drives every `run_*` test, and stage 8's discovery tests used the real SQLite store instead
because the fake had no caller yet.

## Stage 10 — Report factories, example jobs, and the README

`reports/manufacturing.py` with the `WAFER_FAILURE` factory, the two example job scripts from
[05 §6](05-discovery-and-processing.md#6-example-job-scripts), and a user-facing README covering
install (with the pip git-cache gotcha), the two-phase job model, and how to write a block.

**Done when:** a reader who has never seen the code can follow the README to a generated report in a
scratch directory, and the README's install commands are the ones CI actually runs.

## Stage 11 — Optional backends

`infrastructure/stores/postgres_store.py`, `infrastructure/writers/s3_writer.py`, and the shared
behaviour suite extended to both. Both remain self-skipping in CI without their services.

**Note for this stage:** `AUTO_INFORM_OUTPUT_KIND=s3` currently raises in `build_output_writer`; replace
that with the real `S3Writer` branch here, and keep the failure mode loud — refusing to start beats
silently writing locally.

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

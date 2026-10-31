---
status: active
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
sends entities into `<style>`, where they are not decoded (ADR-0024). `auto_html_report/_naming.py` was
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
comment claiming a bare `import auto_html_report` was flagged in every rule style was false under the denylist
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
`AUTO_HTML_REPORT_ERROR_DETAIL` is silently dead until this stage connects it — and a test should prove the
setting reaches the rendered cell, not merely that the parameter exists.

**Review boundary:** at this point the library is genuinely usable for someone willing to pass
explicit blocks. Everything after this stage is the scheduled-job story. **Completed 2026-10-18**

**Amended during implementation:** three things. `generate_report` and `report_filename` live in
`auto_html_report/generate.py` at the **package root**, not in `application/`, because they compose collection,
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
from `auto_html_report`** — the module docstring already promised them and `05 §6`'s job scripts import them, so
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
`build_output_writer` **raises for `AUTO_HTML_REPORT_OUTPUT_KIND=s3`** instead of falling back to the local
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

**Noted, not fixed, with owners:** the remaining observations from the security review are scheduled
rather than forgotten. **Stage 9** must pass `limit` on every `pending()` call and decide whether the port
needs a defensive cap — an unbounded read plus one giant `save()` transaction can hold SQLite's write lock
long enough for overlapping processing to hit the busy timeout — and must either state that one run per
process is a requirement or hold a process-wide executor, so an abandoned worker occupies a slot instead
of adding a thread per run. **Stage 11** must add a redaction filter before the real HTTP client lands, or
document that discoverer exception text must not contain credentials. The console formatter's
control-character escaping and the retry/attempt bounds were fixed in the second review round, below.

**Second review (2026-10-18, after `c108f9a`):** eight findings, all addressed. The per-source contract
check now covers **every `NOT NULL` field** — `event_id`, `source`, a non-datetime `discovered_at` and a
non-mapping payload — because a NULL `source` reached the store and aborted the whole run, losing the peer
source's events, which is precisely the failure the check exists to prevent. A corrupt stored row now
raises `StoreError` naming the key instead of a raw `JSONDecodeError`. The console formatter escapes
control characters in the message and in every value, so an upstream response body cannot forge a log
line. Retry counts are bounded at the loader **and** in the executor (a count of 2000 overflowed the
hang-guard arithmetic), and the attempt limit at 1000. `O_NOFOLLOW` is applied only where the platform has
it, and the store's POSIX file-mode scope is written down beside the writer's. Two untested properties now
have tests: the same-batch rollback when two feeds claim one key, and the `pending()` tie-break on equal
`discovered_at`.

**Also here:** the discovery events that were missing from the logging contract — `discovery.start`,
`retry`, `failed` and `abandoned` — mirror the block vocabulary the shared executor emits, and
`discovery.finish` gained the `discoverer` field, without which two sources producing the same event
type were indistinguishable in the log. A documented-import test now resolves every
`from auto_html_report import ...` in the documentation set, with a shrinking allowlist for the two names
stage 9 lands; it found a real defect immediately (`register_report` was documented and unexported).

## Stage 9 — The processing job and exit codes

`processing.py`, `exit_codes.py` (both at the package root — [ADR-0030](10-decision-log.md#adr-0030--the-processing-job-composes-at-the-root-and-records-operational-failures)), and the report-level concurrency knob from ADR-0020.

**Done when:** every acceptance criterion in [09 §3](09-testing-and-acceptance.md#3-acceptance-criteria)
that concerns processing passes — including the two that specifically guard against regressions
here: a failed upload not being marked `PROCESSED`, and store calls never originating off the main
thread at any concurrency setting.
**Completed 2026-10-18**

**Amended during implementation:** three things, all recorded in
[ADR-0030](10-decision-log.md#adr-0030--the-processing-job-composes-at-the-root-and-records-operational-failures).
`processing.py` and `exit_codes.py` live at the package **root**, not `application/`: the job must call the
registry and `generate_report`, and the application allowlist excludes the root because the root reaches the
renderer — the same reasoning as ADR-0028. `fail_events_with_failed_blocks` defaults to **`None`** ("use
`AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR`"), not `False`; the documented default would have silently ignored the
setting, so `05 §6`'s script would deliver red reports and exit `0` against a setting that says otherwise.
And a store or writer failure is **recorded and stops the run** rather than escaping: escaping made
`OUTPUT_ERROR` unreachable from `exit_code_for`, while continuing through a writer outage would consume
every event's attempt budget and retire a healthy queue.

**The report-level failure decision:** `generate_report` now emits `report.failed` for **any** exception
after `report.start`, and `report.finish` only when the document was rendered. A refused definition or a
renderer failure therefore ends with `report.failed` instead of an orphaned start; a write failure still
has both, the finish carrying `output_uri: null`. The runner owns the event-level line
(`event.failed`, and `event.skipped` when no attempt was counted). `07 §4` describes all of it.

**Also landed:** the shared store suite runs against `FakeEventStore` as well as SQLite, so the double is
held to the port rather than to the runner's assumptions.

**Stage-8 obligations settled here:** `run_processing` always passes a concrete limit —
`AUTO_HTML_REPORT_PROCESSING_LIMIT` (default 500); an explicit `limit` can lower it, never raise it, and a larger
request is clamped and logged as `processing.capped`. `max_workers` and `max_concurrent_reports` now have
maxima (64 and 32), because each report starts its own block pool and the two multiply. The report-deadline
question is answered with a documented **no**: blocks are bounded by their budget and the hang guard,
factories are documented as pure and fast, and a hung factory is a defect like a hung `__init__` — recorded
rather than papered over with an invented timeout.

**Review (2026-10-18):** failure classification is now by `isinstance`, so a writer subclass stops the run
and exits `4` while an unrelated class that merely shares the name is `UnclassifiedError` and does
neither; `FailureRecord` is
asserted against both stores in the shared suite; a definition refused because of the event's own data is
`EventDataError`, counted as an attempt, so it retires instead of pinning the head of an oldest-first
queue and sending CI after a deployment problem that does not exist; the batch is bounded and consumed as a
sliding window rather than one future per event; a store that raises anything is still a store failure; and
the console formatter indents the whole traceback block, because a newline inside an exception message
could not be distinguished from the formatter's own structure. Tests were added for two-event isolation, a
capped limit, a subclassed writer, `store.failed`, and the `PENDING` allowlist's own control.
**540 tests.**

**Deferred, with owners:** "failed but delivered" has no URI — the block-error path marks the event failed
while the document exists on disk, and recording it needs an `output_uri` on `mark_failed` (a port change;
an operator can find the file by name meanwhile). Concurrent same-second filename collisions belong to the
naming layer: slug truncation plus a second-resolution timestamp plus an unconditional `os.replace` means
two events can share one URI, and stage 9 is what makes concurrent publication routine — a short hash
segment or a writer that refuses to replace is the fix.

## Stage 10 — Report factories, example jobs, and the README

`reports/manufacturing.py` with the `WAFER_FAILURE` factory, the two example job scripts from
[05 §6](05-discovery-and-processing.md#6-example-job-scripts), and a user-facing README covering
install (with the pip git-cache gotcha), the two-phase job model, and how to write a block.

**Done when:** a reader who has never seen the code can follow the README to a generated report in a
scratch directory, and the README's install commands are the ones CI actually runs.
**Completed 2026-10-18**

**Amended during implementation:** the factory uses the two blocks that exist — `WaferMapBlock` and
`MaintenanceHistoryBlock`. [03 §3](03-report-api.md#3-predefined-reports-dynamic-lists-and-per-event-reports)
sketched a third `TrendChartBlock`, which was never built; the sketch now matches the module, because a
specification example that names a class nobody can import is how a reader loses an afternoon.

The examples run rather than merely illustrating: `examples/discover_wafers.py` and
`examples/process_events.py` wire the v1 stub client to canned fixtures (`examples/demo_api.py`), so the
whole pipeline — two failed lots, two reports with a chart and a maintenance table — executes with no
network. `examples/quick_start.py` is the README's quick start as a program, and it is what the packaging
check runs.

**Second review (2026-10-18):** the branch as committed was missing the report package. `.gitignore`'s
unanchored `reports/` — meant for the generated output directory — also matched
`src/auto_html_report/reports/`, so the files were ignored, never entered the commit, and a clean checkout died
at collection while the working tree stayed green. The patterns are anchored now (`/reports/`, `/dist/`,
`/build/`) with the reason written beside them, the packaging check imports `auto_html_report.reports.manufacturing`
from the installed wheel so a missing subpackage fails there, and the accidentally committed `FIXME.md` is
untracked and ignored. CI's install check now prints the new `auto_html_report.__version__`, computed from
`get_version()` rather than a second copy. The README's Check-column sentence and its configuration table
were corrected, and `03 §3` regained the ad-hoc example that had illustrated the "dynamic lists" half of
its title.

**Review (2026-10-18):** the documented demo command was broken — `python examples/discover_wafers.py`
puts `examples/` on `sys.path`, not the repository root, so the shared fixtures failed to import, and the
in-process tests hid it. The README and the scripts now use `python -m examples.…`, with a subprocess test
that runs the documented command. The review also found setup failures escaping `main()` as tracebacks
instead of exit `2` (both scripts now load settings inside the guard), a factory that turned a `null`
payload field into the lot id `"None"` (fields are type-checked, and the tests cover absent, null,
wrong-typed and empty), and a `docs/03` snippet that still disagreed with the module.

The packaging claim needed machinery to be true. `scripts/check_install.sh` builds the wheel, installs it
into a scratch environment, and runs the quick start **from the installed package**, catching what a
source-tree test cannot (a template missing from the wheel, an import that only resolves because the
repository is the working directory). `.github/workflows/ci.yml` ships the checks from
[09 §5](09-testing-and-acceptance.md#5-ci), that script, and a git-URL install pinned to the commit —
`git+file://$GITHUB_WORKSPACE@$GITHUB_SHA`, the README's install form without needing the remote or a
token. The README quotes the development commands verbatim, which is the only way "the commands CI runs"
stays true.

## Stage 11 — Optional backends

`infrastructure/stores/postgres_store.py`, `infrastructure/writers/s3_writer.py`, and the shared
behaviour suite extended to both. Both remain self-skipping in CI without their services.

**Note for this stage:** `AUTO_HTML_REPORT_OUTPUT_KIND=s3` currently raises in `build_output_writer`; replace
that with the real `S3Writer` branch here, and keep the failure mode loud — refusing to start beats
silently writing locally. **The S3 client must set transport timeouts**: writers are called from
concurrent report workers and the runner has no per-report deadline ([06 §1](06-output-html.md#1-writers),
ADR-0030), so a stalled
upload would wedge the job rather than produce an `OutputError`.

**Done when:** the store suite passes against PostgreSQL when a DSN is provided, and the S3 writer
passes against an injected stub client with no network.
**Completed 2026-10-18**

**Amended during implementation:** both adapters import their optional dependency when constructed and
validate their configuration there (ADR-0031). `S3Writer` gained `timeout_seconds` (default 30 s) and
creates the boto3 client in `__init__`, so a missing extra or a stalled upload surfaces at start-up rather
than mid-run. The "plain filename" rule moved to `writers/_names.py`, shared by both writers instead of
duplicated. Both classes are exported from the package root; importing the root does not import psycopg
or boto3.

**Verified against a real server, not only the fake.** The shared store suite ran against SQLite, the
in-memory double and PostgreSQL 17 in a container: `AUTO_HTML_REPORT_TEST_POSTGRES_DSN=… uv run pytest -m
postgres`. That run caught a real bug — psycopg3's `with connection:` **closes** the connection on exit,
so the store's schema setup left it holding a closed handle. Setup now commits explicitly. The S3 path is
exercised end to end through `run_processing` with a stub client, and a recorder proves the upload
happens before `mark_processed`. The two example jobs also run against PostgreSQL, producing the same two
reports as the SQLite path.

**Second review (2026-10-18):** three P1s, all PostgreSQL. `pending()` was the only method outside a
transaction, so a read left a session idle-in-transaction (holding a snapshot and a lock) and a failed
read left it aborted for the next call; it now commits like every other call. `discovered_at` decoded in
the **session** timezone, not UTC — invisible in the UTC test container, caught by a test that sets
`timezone=Europe/Berlin` and asserts the round-trip is still UTC. And the suite dropped `events` in
whatever database the DSN named; it now owns a dedicated `auto_html_report_test` schema, so a developer
pointing at their real queue cannot lose it.

Two P2s: the SQLite store still refused PostgreSQL with "arrives with stage 11" (and both examples
hardcoded SQLite), and the refusal tests were marked `postgres`, so they never ran; both fixed, the
examples choose their store from `AUTO_HTML_REPORT_EVENT_STORE`. A P3 on each side was worth taking too: the
report filename gained an eight-character digest over the *raw* title and event id, so two distinct
events whose slugs collide can no longer overwrite each other in a shared bucket; and a malformed
PostgreSQL DSN can no longer put a fragment of the password into a logged `StoreError` (libpq quotes the
token it failed to parse). The store also bounds connect (10 s), statement (30 s) and lock (5 s) waits,
set with `set_config` so an operator's `search_path` survives.

**Second review (2026-10-18):** the two backends disagreed about one input, and that is the finding that
mattered most. Python's `json.dumps` permits `NaN` by default: SQLite stored it as text and read it back,
while PostgreSQL's `jsonb` rejected it with a `StoreError` that aborted discovery's whole persistence phase.
Both stores, the in-memory double and the discovery contract check now use `allow_nan=False`, so a
non-finite number fails that source alone on every backend. Also fixed: `_execute` was the one path that
still put unredacted driver text (and its chained cause) into a `StoreError`; CI now starts a PostgreSQL 17
service so the optional suite runs on every push instead of only by hand; an unknown `output_kind` is
refused rather than reinterpreted as S3; bucket validation rejects IP-like names and the invalid bucket and
encryption cases now have tests; the filename digest is 64 bits with the collision caveat stated; the
wrong-password test edits the DSN structurally instead of assuming the password is `test`; and
`AUTO_HTML_REPORT_OUTPUT_S3_TIMEOUT_SECONDS` makes the transport timeout reachable from the documented factory
instead of only from a hand-built writer.

**638 tests with PostgreSQL (which CI now starts); 606 passing and 32 skipped without a DSN.**

**Post-v1 conformance pass (2026-10-18).** With the last stage merged, the specification was audited
against the code rather than assumed. Three gaps were closed. `collect_blocks` and `render_report` — which
[03 §2](03-report-api.md#2-the-three-public-entry-points) calls *public* and promises so that a caller need
not reimplement the pipeline — were not exported from the package root; they are now, with a module-scope
import test. The two stage-8 deferrals assigned to later stages are also settled: [05
§4](05-discovery-and-processing.md#4-throughput-policy) now states that a job is **one run per process**
(the runner abandons a wedged worker rather than reaping it, so a long-lived loop would accumulate
orphaned threads), and [05 §1](05-discovery-and-processing.md#1-discovery) states that discoverer
**exception text is logged unredacted** and must not contain credentials. What remains is not
implementation: the document set was ratified on the same day and all eleven documents moved to `active`;
the seven open questions are accepted deferrals with named v1 defaults, listed in
[README.md](README.md#open-questions-index). The uv binary version is still not pinned beyond the
`uv_build` bound in `pyproject.toml`, and no release tag exists yet.

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

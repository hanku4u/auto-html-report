---
status: active
description: ADR-0001 to ADR-0035 — each ratified design decision with its context, the alternatives rejected, and the consequences accepted.
replaces: docs/SPEC.md §11, §12
---

# 10 — Decision log

Each entry records a fork where the alternative was genuinely defensible. Recording *why* is what
stops a future contributor from "fixing" a decision that was made deliberately. Read this first when
proposing a design change: the alternatives you are about to suggest have probably already been
considered.

Format: **context** → **decision** → **consequences**. Entries are append-only. A decision is
revisited by adding a new ADR that supersedes the old one, never by editing it.

---

### ADR-0001 — Library-first; no CLI in v1

**Context:** CI needs meaningful exit codes, which usually implies a `main()`.
**Decision:** the library exposes `ExitCode` and `exit_code_for()`, and the client's own job script
(about fifteen lines) calls `sys.exit()`.
**Consequences:** the public surface stays small and every code path goes through the tested API;
each consuming team writes a small script, acceptable at this scale. Revisit when the number of job
scripts exceeds about five and they start drifting.

### ADR-0002 — Blocks split into `collect()` and `render()`

**Context:** a single `build_html()` method would be simpler to write.
**Decision:** split them, because rendering is pure and therefore testable with no network, no clock
and no fixtures.
**Consequences:** two methods per block, and `DataT` is opaque to the framework. In exchange, HTML
generation bugs are caught by fast unit tests instead of in a scheduled job at 3 a.m.

### ADR-0003 — Internal registry with a package scan, not `entry_points`

**Context:** "plugin system" could mean external distributions, but this is an internal tool where
blocks are added directly to the package.
**Decision:** `@register_block` plus a `pkgutil` scan of `auto_inform.blocks`, with lazy, idempotent
lookups.
**Consequences:** zero packaging ceremony to add a block — create a module, decorate a class, done.
The `Block` interface is still the plugin interface, so moving to `entry_points` later changes only
`registry.py`.

### ADR-0004 — A failing block renders an error cell; it never aborts the report

**Context:** partial data versus no data.
**Decision:** isolate failures; operators who want strictness get it through the exit code
(`AUTO_INFORM_FAIL_ON_BLOCK_ERROR`), not by losing the artefact.
**Consequences:** reports are always produced, so a broken block is quietly red until someone reads
the log — mitigated by structured `block.failed` lines and a non-zero exit code.

### ADR-0005 — Threads with cooperative timeouts; not processes, not asyncio

**Context:** blocks are synchronous and API-bound, so parallelism is required, and a runaway block
must not hang the job.
**Decision:** `ThreadPoolExecutor`, with the timeout enforced inside the worker and an outer
backstop.
**Consequences:** a timed-out block's thread is abandoned, not killed — so every HTTP call must carry
a client-side timeout, enforced by making `timeout` a required keyword on `HttpClient`. Rejected
`ProcessPoolExecutor` because payloads would need to be picklable and matplotlib lifecycle in
subprocesses is genuinely awful; rejected asyncio because it would force every block author to write
async code and get cancellation semantics right.

### ADR-0006 — Generic event envelope with an opaque JSON payload; not typed subclasses

**Context:** new event types arrive constantly, and the store must handle types it has never heard
of.
**Decision:** `Event(event_type, event_id, payload, discovered_at, source)`.
**Consequences:** adding an event type requires no migration and no framework change. The cost is
that payload shape is validated by each `ReportFactory`, not by the framework, so that validation
must be explicit and is the factory's responsibility.

### ADR-0007 — `EventStore` port with SQLite default and optional Postgres

**Context:** two independently scheduled jobs must communicate, and production already has Postgres.
**Decision:** define the port once; SQLite is the zero-configuration default so the framework works
in a dev container, Postgres is an extra requiring a client-supplied DSN. The port is
**main-thread-only** and single-writer; claim/lease semantics are explicitly out of scope for v1.
**Consequences:** either phase can be re-run, throttled or failed independently, and retries are
natural because an event stays `NEW` until successfully processed. The cost is a stateful component
in a previously stateless library, and a documented inability to run two job *processes* against the
same store concurrently. Two implementations to keep honest — mitigated by a shared behaviour suite
and an identical schema.

### ADR-0008 — Discovery and processing are separate, store-mediated jobs

**Context:** the naive design has one job discover events and immediately generate reports, which
makes a slow report block throttle discovery.
**Decision:** decouple through the store; the registry maps `event_type` to a `ReportFactory`, and
the factory receives the whole `Event`.
**Consequences:** either phase can be scheduled, throttled, retried or failed independently, and a
half-deployed processing job cannot consume events it has no factory for.

### ADR-0009 — matplotlib with the Agg backend, PNG → base64 data URI; no JS chart library

**Context:** interactive charts (Plotly, Chart.js) versus static images.
**Decision:** static images via matplotlib, lazily imported and serialised behind a module lock.
**Consequences:** fully self-contained, printable, email-safe documents and one charting API for
authors to learn. Costs: larger files, no hover or zoom, and a heavy dependency — accepted, with
lazy import keeping HTML-only reports fast.

### ADR-0010 — Jinja2 for the page shell, escaped by default

**Context:** f-strings versus a template engine.
**Decision:** one Jinja2 template for the document, `autoescape=True`, loaded via
`importlib.resources`.
**Consequences:** one obvious place where HTML is produced, and a single auditable `|safe` in the
whole codebase. Blocks still return raw strings; a per-block template was rejected as ceremony most
blocks do not need.

### ADR-0011 — One block, one row

**Context:** a block could reasonably expand into N rows, or a factory could produce one instance per
row.
**Decision:** one block produces exactly one row in v1.
**Consequences:** the row contract stays trivially simple and failures map one-to-one to cells.
Blocks that need multiple rows emit an inner `<table>` or list inside their single cell — acceptable
visually, and it avoids a second lifecycle concept. Revisit if authors start producing forty-row
inner tables.

### ADR-0012 — `ReportRow.html` is a trust boundary

**Context:** blocks return HTML strings by owner decision.
**Decision:** the framework escapes everything it interpolates; block output is inserted with
`|safe`, and block authors are given escaping-first helpers.
**Consequences:** maximum authoring freedom, and a documented XSS surface that authors must handle.
Acceptable because reports are internal artefacts, but not because it cannot hurt — S3-hosted and
forwarded files are readable outside the team.

### ADR-0013 — Structured (JSON) logging with per-block timing

**Context:** a nightly CI job that silently degrades is hard to diagnose.
**Decision:** stdlib `logging` with a JSON formatter; every block logs start, finish and failure with
`duration_ms`.
**Consequences:** no new dependency, and "which block slowed down" is answerable from the log alone.
Third-party structured-logging libraries were rejected to keep the dependency budget for matplotlib.

### ADR-0014 — Configuration via environment variables only

**Context:** config file, settings object, or environment.
**Decision:** environment only, parsed once into a frozen `Settings`, with redaction in `__repr__`.
**Consequences:** matches CI and container conventions and avoids inventing a config-file format
nobody asked for. Tests construct `Settings` directly, bypassing the environment entirely.

### ADR-0015 — Installed from git, not published to PyPI

**Context:** internal tool.
**Decision:** installed from git, with extras for optional backends:
`uv add "auto_inform[s3] @ git+…@<tag-or-sha>"`. Plain pip remains supported for consumers who do not
use uv.
**Consequences:** no release process to maintain; consumers must pin a tag or SHA, because uv and pip
both cache git sources, and an unpinned `@main` will eventually serve a stale package at the worst
possible moment.

### ADR-0016 — Report definitions are Python, not declarative config

**Context:** YAML report definitions would let non-developers author reports.
**Decision:** Python factories returning `ReportDefinition`.
**Consequences:** full expressiveness — per-event logic, computed parameters, reuse — with no schema
to maintain. The cost is that authoring requires Python, which is fine when the authors are the same
people who write the blocks.

### ADR-0017 — Blocks receive an injected context; they never build clients or read the environment

**Context:** `requests.get(os.environ["TOKEN"])` inside a block is the fastest way to write it.
**Decision:** `ReportContext` carries settings, an HTTP client, a logger and a clock, injected at the
edge.
**Consequences:** blocks are testable with a fake context and cannot casually leak credentials into
logs. Shared across threads, therefore frozen — and `render()` gets a slimmer `RenderContext` with
no HTTP client and no store.

### ADR-0018 — The Check column is structural and empty in v1

**Context:** the column exists for human pass/fail sign-off that happens *after* the file is produced.
**Decision:** the **renderer** emits `<td class="ai-cell ai-cell--check"></td>` for every row. There
is **no `check` field** on `ReportRow`: a field that can only ever hold `""` is bloat, and the column
is a document-structure concern, not a data-model concern.
**Consequences:** the file is a static artefact in v1; where human input would be *stored* is an open
question, and pretending otherwise in the data model would be a design fiction. The two candidates
identified for storing it later are a sidecar store keyed by `(run_id, block)` — both values are
already emitted in the document header and on each row — or a small service. ADR-0018 reserves the
column, not a field, so making it interactive later changes the renderer and the template only.

### ADR-0019 — Failed events are retried a bounded number of times, then retired

**Context:** `pending()` is oldest-first. An event whose report can never be built stays `NEW`
forever, so it is returned at the head of every run and consumes the budget of everything queued
behind it. In the original specification an `attempts` column existed in the schema but nothing read
or wrote it.
**Decision:** `mark_failed` increments `attempts`; at `AUTO_INFORM_EVENT_MAX_ATTEMPTS` (default 3) the
event becomes `FAILED` and is permanently excluded from `pending()`. The event is logged as
`event.retired`, and its `last_error` is retained for diagnosis.
**Consequences:** a broken event delays others at most three times, and a retired event is visible in
the store and the log rather than silently ignored. Operators who want to retry a retired event must
reset it explicitly — deliberate, because an unbounded retry loop is how a queue dies.

### ADR-0020 — Reports are processed sequentially by default, with an opt-in concurrency knob

**Context:** `generate_report` parallelises blocks within one report, but nothing in the original
specification said how many *reports* run at once, so a run with hundreds of pending events would
serialise completely — the dominant scaling risk in a nightly job.
**Decision:** `AUTO_INFORM_MAX_CONCURRENT_REPORTS` (default `1`) bounds report-level concurrency.
Report building happens in worker threads; **every store call is marshalled onto the main thread**,
so the single-writer guarantee in ADR-0007 holds at any setting.
**Consequences:** the default is safe and trivially correct, and the knob exists for the case that
needs it. Raising it multiplies in-flight HTTP calls by the per-report block pool, so it must be
sized against API rate limits rather than CPU. Sharding a very large job by event type across
several jobs is usually a better answer than one very wide job.

### ADR-0021 — uv owns dependency management; exact pins stay, `requirements.txt` goes

**Context:** the initial specification carried a `requirements.txt` with exact pins, inherited from
spec conventions that belong to a different project. The owner rejected it: this is a Python project
and uv is the tool.

**Decision:** uv manages dependencies. Direct dependencies carry exact `==` pins in `pyproject.toml`;
the resolved transitive closure, with hashes, lives in a committed `uv.lock`; development tools live in
a PEP 735 `dev` dependency group rather than an extra, so they never enter published metadata;
`.python-version` pins `3.11`, the supported floor. The build backend is `uv_build`. `requirements.txt`
is deleted.

**Consequences:** reproducible environments on CI and on developer machines alike, and the classic
"package data missing from the wheel" failure disappears — verified against `uv 0.13.0`, where
`uv_build` packages everything inside `src/auto_inform/` with no `force-include` or `artifacts`
configuration. The subtlety worth remembering, documented in
[08 §2](08-packaging.md#2-dependency-changes): **a lockfile does not constrain consumers.** A package
installed from git resolves against `pyproject.toml` and never reads our `uv.lock`, so the exact pins
were retained rather than replaced by ranges. The project's one deliberate non-exact `requires` is
`[build-system] requires`, which uv itself generates and which never reaches a consumer's runtime.

---

### ADR-0022 — Clock is the framework's single seam for time

**Context:** the specification never defined the `Clock` port's interface, yet stage 2 needed it to
type the contexts, and two requirements depend on it: timeouts and retry backoff must be testable
without real waiting ([09 §2](09-testing-and-acceptance.md#2-test-rules)), and reported
durations must not go negative when the machine's clock is stepped.

**Decision:** three methods. `now()` for wall-clock timestamps a human reads or that must be
comparable across processes; `monotonic()` for measuring *elapsed* time — durations, timeouts,
backoff; and `sleep()` as the only way the framework is permitted to wait.

**Consequences:** retry backoff becomes assertable data instead of three real seconds asleep, and
stage 5 can drive its timeout backstop from the injected clock rather than a real-time wait — which
is what makes the hermeticity rule achievable rather than aspirational. A `Clock` that can sleep is
arguably a "time service" rather than a clock; the name is kept because the layout tree and two
documents already say `Clock`, and churning three specs over a word is not worth it. Rejected: a
separate `Sleeper` port, purer on single-responsibility but a second injected dependency threaded
through four context dataclasses for one concern; and keeping real `time.monotonic`/`time.sleep` with
short real budgets in tests, which works but cannot assert a backoff schedule.

### ADR-0023 — The version is resolved at runtime; no stored literal

**Context:** the layout listed `_version.py` as "single source of the package version" while
`pyproject.toml` also declared `version = "0.1.0"`, and nothing said how two copies would stay in
sync. The HTML footer prints the version, so this had to be settled before stage 3.

**Decision:** `get_version()` reads `importlib.metadata` at runtime and returns `0+unknown` when the
package is not installed. `pyproject.toml` remains the only place a version is written. A
VCS-derived version is deferred with an explicit trigger: adopt it at the first release, or the first
time a tag and `pyproject.toml` disagree, whichever comes first.

**Consequences:** one source of truth, no new dependency, and a footer that cannot claim a version
that was never built — the not-installed path was verified to raise, not assumed. The residual risk is
drift between a git tag and the declared version, which the trigger addresses and which ADR-0015's
tag-pinning convention makes likely to surface quickly. Rejected: a literal `__version__` (a second
copy of the truth, the same failure mode that removed `requirements.txt`); VCS-derived versions now (a
pinned build-time dependency and scaffolding for a release process that does not exist); and threading
the version through `RenderContext` (a documented public signature change for a cosmetic footer).

---

### ADR-0024 — The stylesheet reaches the document through a template include

**Context:** stage 3 found, by inspecting a generated document rather than trusting the test suite,
that the inlined CSS had arrived full of HTML entities: `font-family: &#34;Segoe UI&#34;` and
`&lt;style&gt;` inside a comment. The stylesheet was being injected as a template *variable*, so
autoescaping escaped it -- and entities are not decoded inside a `<style>` element, so that CSS was
broken, and a future `>` child combinator would have broken it worse. The obvious fix, `{{ stylesheet |
safe }}`, would have added a second `|safe` filter and quietly falsified the single-trust-boundary
claim in §4 of [06](06-output-html.md#4-the-trust-boundary).

**Decision:** the stylesheet is loaded into the Jinja environment as a template and pulled in with
`{% include "report.css" %}`. Literal template text is never autoescaped, so the CSS survives intact,
and row HTML remains the only interpolation in the codebase requiring `|safe`.

**Consequences:** the CSS is parsed as Jinja template syntax, so a future rule containing `{{`, `{%`
or `{#` would break rendering -- loudly, at render time, not silently. Two guards keep the invariant
honest: a test asserts that the template contains exactly one `|safe`, and another asserts the audit of
a rendered document finds no external loads. Rejected: `|safe` on the stylesheet, which trades a real
invariant for a convenience; and inlining the CSS into the template file, which would bury 200 lines of
stylesheet inside the markup it styles.

---

### ADR-0025 — Serialise matplotlib, and save the figure you were given

**Context:** a review of stage 3 found two defects in `figures.py` that the whole test suite had passed
over. The first was a wrong-artefact bug: the helper called `pyplot.savefig`, which takes no figure
argument and saves whatever pyplot considers *current*, so with more than one figure alive it embedded
the wrong chart. Confirmed by probe: the figure it was handed serialised to 4594 bytes, the current
figure to 11317, and the helper returned the latter. Every existing test had exactly one figure alive,
so every existing test passed. The second was that `docs/02` and `docs/06` both claimed the framework
serialises figure *creation*, which it cannot: creation happens in a block's `render()`, through
`plt.subplots()`, in code this module never sees.

**Decision:** `figure_to_data_uri` calls ``figure.savefig``, never the pyplot function. The module owns
one **re-entrant** lock; `figure_to_data_uri` takes it around the save, and block authors take it around
all of their own matplotlib work — create, draw, save, close — through a `figure_lock()` context
manager, which also pins the Agg backend before anything exists. Both documents were corrected to state
that requirement rather than to claim a guarantee the design cannot give.

**Consequences:** the wrong-figure class of bug is closed, and the locking is honest about its extent.
The cost is that charts are serialised across a report, so many charts gain no parallelism — accepted,
because matplotlib's global state leaves no cheap alternative and a process per block is already
rejected in ADR-0005. Re-entrancy matters for usability rather than elegance: the documented pattern
holds the lock across the save, and a plain `Lock` would deadlock there. Rejected: exporting a
higher-level "render my figure" helper, which would have to guess at subplot layouts; and leaving the
lock out of block code, which would keep a real thread-safety hazard in the path stage 4 is about to
write blocks against.

---

### ADR-0026 — The layering rules are enforced by reading imports, and were wrong as written

**Context:** `docs/01` §4 stated four layering rules "enforced by review, and later by an import-linter in
CI" — which in practice meant not enforced at all. Turning them into a check found two problems with the
rules themselves. Rule 2 forbade `application/` from importing `infrastructure/` in any form, yet
`application/context.py` imports `Settings` from it deliberately and for good reason. And rule 1 claimed
the domain layer "can still be imported, tested and reasoned about with no application or infrastructure
layer present", which is unachievable: importing any module under the package runs
`auto_inform/__init__.py`, the composition root, which imports every layer on purpose so that
`from auto_inform import Block` works.

**Decision:** the rules are enforced statically, by `tests/unit/test_layering.py`, which reads each
module's imports with `ast` and separates runtime imports from those under `if TYPE_CHECKING:`. The rules
themselves were corrected: `application/` may reference the `Settings` *type* but never a concrete
adapter; `rendering/` gained an explicit rule; the composition root is named as the one exempt module; and
the domain's standalone-importability claim was replaced with the fact it actually guarantees — the domain
modules do not depend upward.

**Consequences:** the invariant is real now, and specific enough to be useful: the domain exception is
*pinned* to the two documented context names, so a third fails the test and forces the document to change.
Each fix is guarded against a vacuous pass by negative controls, because an audit that silently stopped
objecting is worse than no audit. Rejected: the import-hook test originally planned, for the
composition-root reason above; and an off-the-shelf import linter, which would have added a pinned
dependency to express five rules that fit on a page.

---

### ADR-0027 — The last-resort hang guard waits on real time

**Context:** ADR-0022 made `Clock` the framework's single seam for time and said `sleep()` is the only way
the framework is permitted to wait. That was taken at face value when stage 5 began: the collector was
going to poll its futures, waiting through `clock.sleep()` and measuring the deadline with
`clock.monotonic()`, so timeout tests would be entirely fake-driven. A probe killed the idea in one run.
With a 61 s budget, a caller polling every 10 ms advanced a fake clock by **622 s in the 50 ms** a
real-but-healthy worker took, and abandoned a block that was working perfectly. The mechanism does not
merely make tests awkward — it makes the guard wrong, because the caller's own polling races its own
deadline, and any block doing real work (a chart, a store round-trip) advances the fake clock not at all.

**Decision:** two kinds of time, deliberately. The per-attempt budget and retry backoff run on the
injected `Clock` — fake-driven, hermetic, assertable as data. The outer hang guard passes a **real-time**
timeout to `future.result()`, computed as every attempt's budget plus the worst-case backoff plus a grace
period.

**Consequences:** one test costs real milliseconds (a stuck block, released at teardown after ~1 s),
and every other timing test stays fake-driven. The stronger argument is not convenience, though: **a
deadline measured on a clock the wedged code controls is not a hang guard.** A block stuck inside
`collect()` never calls `sleep()` and never advances the clock, so a clock-driven deadline would never
fire — the guard would be disabled precisely when it is needed, and it would be disabled *by the code it
is guarding*. Real time is the only input the wedged code cannot influence. Rejected: polling (broken as
shown); no guard at all, which trades a slow test for the possibility of a hung CI job; and a separate
second clock for the guard, which is the same thing with more machinery.

---

### ADR-0028 — The public entry point composes at the package root

**Context:** `generate_report` collects blocks, assembles the report, renders it and writes it. It is
named in the specification as living in `application/generate_report.py`, but `application/` may not
import `rendering/` (rule 2 of [01 §4](01-overview.md#4-architectural-overview)), and composing a document
means calling the renderer. The layering audit refused the documented placement on the first run — the
same way it refused the collector's original error-cell design a stage earlier.

**Decision:** `generate_report` and `report_filename` live in `auto_inform/generate.py` at the package
root, beside `registry.py`. The root is the composition point: it may see every layer, provided it
acquires no infrastructure adapter — and it does not, because the writer arrives as an injected
`OutputWriter`. The use case of gathering data stays in `application/collect_blocks.py`.

**Consequences:** the dependency direction is honest — application logic never depends on HTML, and the
one module that needs both is explicitly the composition point rather than a layer pretending to be
application code. Rejected: widening the application allowlist to permit `rendering/`, which would let
any application module grow a Jinja dependency; and inverting the dependency with an injected renderer, a
seam with exactly one implementation and no second caller in sight.

---

### ADR-0029 — Timeout, retry and hang-guard mechanics live in one executor

**Context:** Blocks and discoverers are subject to the same execution rules
([02 §6](02-block-authoring.md#6-execution-semantics-for-one-report),
[05 §1](05-discovery-and-processing.md#1-discovery)). Stage 5 implemented them inside `collect_blocks`;
discovery would have needed the same loop again, including the budget check that also runs when an
attempt *fails*, the backoff schedule, and the real-time hang guard from
[ADR-0027](#adr-0027--the-last-resort-hang-guard-waits-on-real-time). Two copies of that logic do not
stay equal: the second copy is where the retry policy quietly stops applying.

**Decision:** The mechanics live in `application/execution.py`. A caller supplies
:class:`~auto_inform.application.execution.Task` values -- an attempt callable, an optional pure
finalise step such as `render()`, and any per-task overrides -- plus an
:class:`~auto_inform.application.execution.ExecutionVocabulary` naming its log events (`block.*` or
`discovery.*`), the field that identifies a task, and the stage that appears in a timeout message.
`collect_blocks` and `run_discovery` map the shared result to their own outcome types.

**Consequences:** one place to fix a retry bug, and a new kind of concurrent work cannot acquire a
different timeout policy by accident. The cost is one layer of indirection between a block and its retry
loop, visible in the signatures rather than in behaviour. Rejected: a second loop in `discovery.py`
(duplicate logic, guaranteed to drift); and a generic callback interface with five logging methods per
caller, which is more surface than one vocabulary dataclass for the same separation.

---

### ADR-0030 — The processing job composes at the root, and records operational failures

**Context:** Stage 9's plan placed the job in `application/processing.py`. It must call the registry
(`registry.py`) and `generate_report` (`generate.py`), both package-root modules, and `application/`'s
allowlist excludes the root precisely because the root reaches the renderer (rule 2 of
[01 §4](01-overview.md#4-architectural-overview)). Separately, `05` §2 rule 5 said a failing writer
"escapes" the run, while its §5 table ranked `OUTPUT_ERROR` for a report that could not be written — a run
that has been abandoned cannot be ranked.

**Decision:** `processing.py` and `exit_codes.py` live at the package root, beside `generate.py` and
`registry.py`, for ADR-0028's reason: composing is the root's job, and a layer should not pretend to be
application code to reach the renderer. The runner **records** a per-event operational failure — a missing
factory, a refused definition, a write failure, a store call that fails during the commit phase — in
`EventProcessingOutcome`, and `exit_code_for` ranks the recorded `error_type`s. A store or writer failure
stops the run after one recorded outcome, because both are systemic: every later event would fail the same
way, and continuing through a writer outage would burn every event's attempt budget and retire a healthy
queue. Only a `pending()` failure escapes, because no outcome exists to report on. `mark_failed` returns
`FailureRecord(attempts, retired)` so `event.failed` and `event.retired` carry the store's own count.

**Consequences:** the documented exit codes are producible by the documented `exit_code_for(run)` call,
and a script that wants codes for setup failures wraps its own startup (`05` §6 shows how). A write failure
no longer aborts the *process*, but it does end the batch after one attempt was consumed — the smallest
price that still reports `OUTPUT_ERROR`. `fail_events_with_failed_blocks` defaults to `None` (meaning "use
`AUTO_INFORM_FAIL_ON_BLOCK_ERROR"`) rather than the documented `False`, which would have silently ignored
the setting. A run is bounded by `AUTO_INFORM_PROCESSING_LIMIT` (default 500); an explicit `limit` can
lower it and never raise it, and a caller that asks for more is clamped with a `processing.capped` line.
Failure classification is by `isinstance`, not by exception name: a writer subclass is filed with
`OutputError`, a definition refused because of the event's data is `EventDataError` — ranked as a partial
failure and counted as an attempt, so it retires rather than sitting at the head of an oldest-first queue
for ever — and a class from another library that merely shares a framework name is `UnclassifiedError`,
so it can neither stop a run nor select an exit code. Rejected: widening the application allowlist to the root (it would give every application
module a path to rendering); and leaving `mark_failed` returning `None` (the retirement boundary would be
re-derived in the runner, in a second place, and could drift).

---

### ADR-0031 — Optional backends import lazily and construct eagerly

**Context:** `auto_inform` installs from git with optional `postgres` and `s3` extras, and `import
auto_inform` must work without either — a block author using only HTML tables should not need psycopg.
At the same time, the processing runner waits for `write()` with no per-report deadline (ADR-0030), so an
S3 client without transport timeouts turns a stalled upload into a wedged job; and a job configured for a
backend it cannot reach should fail at start-up, not on its first event.

**Decision:** the adapter modules import `psycopg` and `boto3` when an adapter is **constructed**, not
when the package is imported, and an injected client skips the import entirely. Construction does the
I/O that validates the configuration: `PostgresEventStore` connects and applies the schema,
`S3Writer` creates the boto3 client with bounded `connect_timeout`/`read_timeout` (30 s by default,
`timeout_seconds=` to override) and a small retry count. A missing extra, a bad DSN, an empty bucket or a
non-positive timeout raises `ConfigurationError` at that point.

**Consequences:** the base install stays light, `import auto_inform` cannot fail because an extra is
absent, and tests exercise both adapters without network or credentials (a stub client; a self-skipping
DSN). The store also bounds the waits the runner cannot: a 10 s connect timeout, a 30 s statement timeout
and a 5 s lock timeout, set with `set_config` so a `search_path` in the operator's DSN survives. The cost
is that constructing an adapter performs I/O — which is the point, because a setup failure should happen
during setup. Rejected: module-level imports of the optional dependencies (breaks the base
install) and lazy client creation (moves a configuration failure to the first report, after work has been
done).

---

### ADR-0032 — Published to public PyPI as `auto-html-report`

**Context:** ADR-0015 kept this an internal tool installed from git, which gave it no release process and
no index presence. The owner now wants a package anyone can install by name, and has approved releasing the
source publicly. The repository is public, with no tags, so nothing depends on the old name.

**Decision:** supersedes ADR-0015. The package is published to PyPI under Apache-2.0, chosen over MIT for its
explicit patent grant. Every name moves together, with no compatibility shim: distribution `auto-html-report`,
import `auto_html_report`, environment prefix `AUTO_HTML_REPORT_`, and the repository. Publishing uses trusted
publishing from a tag-triggered workflow, with owner approval on the production environment. See
[12](12-publishing.md).

**Consequences:** consumers get `pip install auto-html-report` and normal version resolution, and this project
takes on a release process and a compatibility obligation once `0.1.0` ships. Rejected: a private index (the
owner wants it open source); GitHub Releases only (no version resolution for consumers); renaming only the
distribution (`uv_build` infers the module from the project name, and two names for one package is a permanent
cost paid to avoid a one-time one).

---

### ADR-0033 — Runtime dependencies use ranges; the lockfile pins this repository

**Context:** ADR-0021 kept exact `==` pins on runtime dependencies because a lockfile does not constrain
consumers. That reasoning is correct, and its conclusion suits an application. For a library on an index it
fails: a consumer with any other numpy or boto3 version cannot resolve the package at all, and boto3 in
particular releases almost daily.

**Decision:** supersedes ADR-0021's pinning rule; the rest of ADR-0021 stands (uv, `uv_build`, the committed
lockfile, the `dev` group). Runtime dependencies and extras declare a tested floor and an upper bound at the
next major version. Development tools keep exact pins, because they never reach a consumer. A CI job at
`--resolution lowest-direct` keeps the floors honest.

**Consequences:** the package can coexist with what consumers already use, and determinism for an application
becomes that application's own lockfile, which is where it belongs. The cost is that a consumer can meet a
combination we did not test, which the matrix and the floor job narrow but cannot eliminate. Rejected: no upper
bounds (a new major version of a dependency breaks every existing install at once); and floors chosen by
judgement, untested (a floor nothing exercises is a guess that reads as a promise).

---

### ADR-0034 — The version stays a static literal, checked against the tag

**Context:** ADR-0023 deferred VCS-derived versions with a trigger: adopt them at the first release. That
release is now planned, so the trigger has fired. Tested against `uv 0.13.6`, `uv_build` refuses
`dynamic = ["version"]` with ``missing field `version` ``, so a derived version would mean replacing the build
backend.

**Decision:** resolves ADR-0023's trigger by declining it. The version remains a literal in `pyproject.toml`,
set with `uv version --bump`, and the release workflow fails if the tag differs from it.

**Consequences:** drift between tag and version, the risk the trigger existed to remove, becomes a failed
release before anything is published instead of a structural impossibility. Keeping `uv_build` also keeps its
verified template packaging (ADR-0021). The cost is one manual bump per release, in a reviewable commit.
Rejected: `hatch-vcs` or `uv-dynamic-versioning` on another backend (a new build dependency and a re-verification
of package data, to save one line in a PR).

---

### ADR-0035 — Consumers bring their own HTTP client, and forgetting one says so

**Context:** v1 ships only `StubHttpClient`, and `new_report_context()` uses it when `http` is omitted. That
default was harmless while the package was internal, but a public consumer who forgets `http=` is told that a
*fixture* is missing, and the `HttpClient` protocol they would need to implement is not exported. Requiring
`http` outright would break the quick start and any report whose blocks do no HTTP.

**Decision:** the package ships no real client. `HttpClient` and `Clock` are exported; `StubHttpClient` moves to
a public `testing` module; `http` stays optional, and its default becomes a client that raises
`ConfigurationError` on use, naming `http=`. The block-authoring document gains an adapter recipe.

**Consequences:** no HTTP dependency, and no decisions about authentication or connection pooling made on behalf
of APIs this project has never seen. Each consumer writes a short adapter and is responsible for the error
mapping in [02 §5.1](02-block-authoring.md#51-normative-error-mapping), which is what retry behaviour rests on.
Rejected: an `httpx` client behind an extra (more surface to maintain, and authentication would still need
designing); raising at context construction (breaks reports that need no HTTP).

---

## Corrections carried into this document set

These are not decisions. They are defects in the original single-document specification, plus one
defect introduced by the split itself, recorded here so that a reviewer of the split can confirm
each one was actually addressed rather than quietly dropped.

| Defect in the original | Fixed in |
| --- | --- |
| `HttpClient` was referenced five times but never defined; block authors could not write a block | [02 §5](02-block-authoring.md#5-the-httpclient-port) |
| Retry classification contradicted itself: a comment said "5xx is retryable" while the retryable set contained no HTTP error type, so a 503 would have been treated as permanent | [02 §5.1](02-block-authoring.md#51-normative-error-mapping) |
| `collect_blocks` and `render_report` were declared public with no signature | [03 §2](03-report-api.md#2-the-three-public-entry-points) |
| No policy for report-level throughput | ADR-0020 |
| Failed events could starve the queue; the `attempts` column was unused | ADR-0019 |
| Filename generation had no owner | [06 §2](06-output-html.md#2-filename-and-destination) |
| `Settings` attribute names were never mapped from environment variables | [07 §1.1](07-configuration-and-logging.md#11-environment-variable-to-settings-attribute-map) |
| `render()` was called pure while receiving a logger | [02 §3](02-block-authoring.md#3-render--the-pure-phase); the logger was removed from `RenderContext` — see ADR-0017 above |
| The store port had no lifecycle (`close()`) or thread-affinity statement | [04 §2.4](04-events-and-stores.md#24-thread-affinity) |
| An acceptance criterion asserted no `http://` anywhere in the document, which would false-fail a legitimate link to a ticket system | [06 §4.1](06-output-html.md#41-what-self-contained-means-precisely), [09 §3](09-testing-and-acceptance.md#3-acceptance-criteria) criterion 10 |
| `ReportRow.check` could only ever hold `""` | ADR-0018 |
| **Introduced by the split:** the context dataclasses from the original §4.4 (`ReportContext`, `RenderContext`, `DiscoveryContext`, `RunMetadata`) and their thread-safety gotcha were referenced throughout the new set but defined nowhere, so no document specified what a block actually receives | [03 §5](03-report-api.md#5-the-injected-contexts) |
| **Introduced by the split:** the "CI is recommended, not mandated" scope caveat was dropped, silently hardening a recommendation into a requirement | [09 §5](09-testing-and-acceptance.md#5-ci) |
| `AUTO_INFORM_ERROR_DETAIL=detailed` was said to add the exception's `__cause__` chain, which no renderer can do: `BlockOutcome` carries a summarised type and message and no cause chain | [06 §6](06-output-html.md#6-row-check-and-error-markup) |
| The `data-block` example did not match its own row's Item text, leaving it ambiguous whether the attribute identifies the block type or the row | [06 §6](06-output-html.md#6-row-check-and-error-markup) |
| `data-block` uniqueness was claimed as the basis for a future `(run_id, block)` sign-off key, but slugging distinct Item texts can collide (`R&D maintenance` and `R D maintenance`, or two long texts sharing a prefix), so uniqueness was never actually enforced | [06 §6](06-output-html.md#6-row-check-and-error-markup) |
| `02` and `06` claimed the framework serialises figure *creation*; the framework can only serialise the saving it performs itself | [02 §2](02-block-authoring.md#2-collect--the-io-phase), [06 §5](06-output-html.md#5-figures) |
| `06 §4` advertised `from auto_inform import escape, html_table, figure_to_data_uri` while the package exported nothing | [06 §4](06-output-html.md#4-the-trust-boundary) |
| `detailed` was said to show "the whole recorded message", but no `white-space` rule preserved its newlines, so it rendered as one run-on line | [06 §6](06-output-html.md#6-row-check-and-error-markup) |
| Rule 2 forbade `application/` from importing `infrastructure/` at all, yet `application/context.py` imports `Settings` from it deliberately — the rule was false as written | [01 §4](01-overview.md#4-architectural-overview), ADR-0026 |
| Rule 1 claimed the domain layer could be imported with no application or infrastructure layer present, which the composition root makes impossible | [01 §4](01-overview.md#4-architectural-overview), ADR-0026 |
| The layering rules were documented as enforced "by review, and later by an import-linter in CI" — a euphemism for unenforced, until `test_layering.py` existed | [09 §1](09-testing-and-acceptance.md#1-strategy-by-layer) |

## Open questions

None. Questions that were open in the original specification have been resolved into the ADRs
above; each document's own unresolved questions are indexed in
[README.md](README.md#open-questions-index).

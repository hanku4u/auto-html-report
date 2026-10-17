---
status: proposed
description: ADR-0001 to ADR-0020 — each ratified design decision with its context, the alternatives rejected, and the consequences accepted.
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
**Decision:** `pip install "auto_inform @ git+…"`, with extras for optional backends.
**Consequences:** no release process to maintain; CI must pin a tag or SHA, because pip caches git
installs and an unpinned `@main` will eventually serve a stale package at the worst possible moment.

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

## Open questions

None. Questions that were open in the original specification have been resolved into the ADRs
above; each document's own unresolved questions are indexed in
[README.md](README.md#open-questions-index).

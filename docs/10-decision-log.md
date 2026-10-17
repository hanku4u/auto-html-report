---
status: proposed
description: ADR-0001 to ADR-0025 — each ratified design decision with its context, the alternatives rejected, and the consequences accepted.
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
without real waiting ([09 §2](09-testing-and-acceptance.md#2-hermeticity-rules)), and reported
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

## Open questions

None. Questions that were open in the original specification have been resolved into the ADRs
above; each document's own unresolved questions are indexed in
[README.md](README.md#open-questions-index).

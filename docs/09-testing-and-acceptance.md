---
status: proposed
description: The per-layer testing strategy, hermeticity rules, and the acceptance criteria that define v1 as done.
replaces: docs/SPEC.md §9, §13
---

# 09 — Testing and acceptance criteria

## 1. Strategy by layer

| Area | Approach |
| --- | --- |
| Domain objects | Plain unit tests: duplicate block names rejected, empty report rejected, frozen semantics hold, `all_succeeded` and `failed_rows` agree with row statuses. |
| Registry | `tests/unit/test_registry.py`. Assert a duplicate `display_name` is refused, re-registering the same class is a no-op, the built-in scan is idempotent, an unknown key reports what *is* registered, and `available_blocks()` hands out a read-only view. The registries are process-global by design, so the module snapshots and restores them around every test: otherwise a temporary block leaks into every later test in the same process. |
| `collect_blocks` | Fake clock and fake callables. Assert: ordering preserved when the first block is slowest; one failure does not prevent others running; retry count and backoff respected; timeout yields `TIMED_OUT` without aborting the run. |
| HTTP error mapping | The normative table in [02 §5.1](02-block-authoring.md#51-normative-error-mapping) is tested status by status against the client: a 503 raises a retryable error, a 404 does not, a 429 is retried, a malformed body is permanent. |
| Runners | `FakeEventStore` (in-memory dict, same semantics as the SQL schema). Assert re-discovery does not reset `PROCESSED`; a failed write leaves the event `NEW`; a missing factory skips rather than consumes; report concurrency above 1 never writes to the store off the main thread. |
| Stores | Integration tests against a temporary SQLite file, and the same shared behaviour suite against the in-memory double and PostgreSQL. PostgreSQL tests are marked `@pytest.mark.postgres` and skipped unless `AUTO_INFORM_TEST_POSTGRES_DSN` is set. |
| Writers | Local writer: temp dir, atomic rename, directory creation. S3 writer: injected stub client, key composition, content type, encryption header. No live AWS. |
| Rendering | Golden-file tests for the full document with a two-block fixture report. Normalise insignificant whitespace before comparing, **and normalise the footer's version token**, so that cutting a release does not break the golden files. A comparison that gets loosened every release ends up asserting nothing. This is the output contract in [06](06-output-html.md) made executable. |
| Figures | Smoke test only: build a figure, assert the URI starts `data:image/png;base64,`, and assert no figures remain open afterwards. Never assert on pixels. |
| Thread safety | Sixteen blocks in parallel, half of which render figures: no exception, stable row order. This is the test that catches a missing figure lock. |
| Configuration | Explicit `Settings` mappings, never `os.environ`: `load_settings` is tested with a mapping for every documented variable, for reporting every invalid one in a single exception, for the cross-field requirements, for numeric bounds (including that a zero retry count is *allowed*), and for secret redaction in `repr`. |
| Logging | The JSON formatter's envelope and structured fields, the traceback going to the log line rather than into a report, and `configure_logging` being idempotent while leaving a host's own handlers alone. |
| HTTP stub | Fixtures by path, a loud failure for an unknown path, `get_bytes` refusing a non-bytes fixture, `raising_for`, the required `timeout` keyword, and eight threads sharing one instance. |
| Layering | `tests/unit/test_layering.py` reads every module's imports and asserts the rules in [01 §4](01-overview.md#4-architectural-overview), with a negative control so the audit cannot pass vacuously. An import hook cannot do this job: importing any submodule runs the composition root, which wires every layer on purpose. |
| Documentation set | `tests/unit/test_doc_conventions.py` and `test_doc_links.py` assert what used to be checked by hand — that the `pyproject.toml` snippet equals the real file, that the ADR range quoted in two places matches the entries, that numbering and frontmatter hold, that oversized documents carry a Dev plan, and that every cross-reference resolves to a heading that exists. |
| Time doubles | `tests/support/clocks.py` provides the `FakeClock`, whose contract is specified in [§1.1](#11-the-fakeclock-contract) and pinned by its own tests. |
| Entry point | `tests/unit/test_generate_report.py`, `test_report_filename.py`, `test_local_writer.py`. Assert the document written equals what was returned; that no writer means no file and no location; that a failed block still produces a written report; that the filename rule caps and slugs; that the writer publishes atomically and refuses a path; and — the wiring stage 5 left open — that `Settings.error_detail` decides whether a multi-line message's second line appears. |

### 1.1 The FakeClock contract

Every timeout and retry-backoff test from stage 5 onward rests on one double, so its three properties are
specified here rather than assumed wherever it is first used:

1. **`now()` and `monotonic()` advance together**, from a single origin. A test that moved one without the
   other would be exercising a clock the framework can never see.
2. **Every requested `sleep()` is recorded, in order.** That is what makes a backoff schedule assertable
   as data — `clock.sleeps == (1.0, 2.0, 4.0)` — instead of something inferred from elapsed real time.
3. **`sleep()` never waits.** It moves the clock instead, which is how a sixty-second timeout is tested in
   microseconds.

Two smaller guards come with it: a negative interval raises rather than rewinding time, and a naive
`start` raises rather than making every rendered timestamp wrong by the host's offset.

**One global timeline, not per-thread time.** Concurrent work advances a *single* clock: four blocks each
advancing a second move it four seconds in total, so a test measuring elapsed time for concurrent blocks
can see a duration inflated by its peers. Timing tests over concurrent work should use generous budgets or
zero simulated work. The readings are locked against lost updates, but the timeline itself is shared by
design — it models a process, and a process has one clock.

## 2. Test rules

These are hard requirements, not preferences. A test that breaks them is a defect. The first five are about
hermeticity — a test that cannot run the same way twice, or that needs the network. The last two are about
strength, and they rest on the same uncomfortable fact: **a test that cannot fail is not a test**, and an
audit that has quietly stopped objecting looks exactly like a suite that is passing.

1. **No network access.** Every HTTP interaction goes through a fake or `StubHttpClient`. A test
   that needs a live API cannot run in CI and must not exist.
2. **No real wall-clock timing.** Timeouts and retry backoff are tested against an injected clock
   and a fake sleep. Asserting that something took at least 30 seconds is not a test, it is a
   delay.
3. **No live external services.** PostgreSQL is the one exception, and it self-skips without
   `AUTO_INFORM_TEST_POSTGRES_DSN`.
4. **No dependence on test order.** In particular, registry tests must not depend on whether
   `import_builtin_blocks()` has already run in the same process. A sharper form of the same rule, learned
   the hard way: **a test that snapshots process-global state must snapshot it in its fully-populated
   form**, because restoring a wiped table and a cleared "already scanned" flag does not repopulate
   anything — the modules are in `sys.modules` by then, so `import_module` returns them from cache and
   their registration side effects never run again. The registry test module did exactly this and failed
   three tests when run alone while the full suite stayed green, which is what this rule predicts.
5. **Deterministic output.** `run_id` and timestamps are injected in tests, so golden files can be
   compared byte for byte.
6. **A test for something that depends on state beyond its arguments needs a competing object alive.**
   When a function touches process-global state — the current figure, a module cache, a connection — the
   test must hold a decoy alongside the subject of the test, or it only proves the function works when it
   is the only thing in the process. Stage 3 shipped a wrong-chart bug exactly that way: every test had
   one figure alive, so `pyplot.savefig` saving the *wrong* figure looked correct for 134 tests
   (ADR-0025). The same trap is waiting in the event store and the template cache.
7. **An audit needs a control that would fail if the audit broke.** Anything asserting an invariant *about
   other code* — the layering rules, the document conventions, the containment check — must ship a probe
   that the audit has to object to, and that probe has to fail if the logic it guards is removed. The
   controls in `tests/unit/test_layering.py`, `test_doc_links.py` and `test_rendering_containment.py` are
   the models. Three examples from this project, every one of them found by probing rather than by the
   suite: the layering audit passed every rule while relative imports bypassed all of them; the anchor
   audit disagreed with the documents it was checking while reporting itself correct; and nothing pinned
   the application rule to its allowlist, so degrading it to "no denylist, no allowlist" would have
   enforced nothing and still passed.

## 3. Acceptance criteria

Each criterion must be verifiable from the repository alone, by a reviewer who did not write the
code.

| # | Criterion | Verified by |
| --- | --- | --- |
| 1 | `uv add "auto_inform @ git+…"` succeeds in a clean environment and `import auto_inform` works | CI packaging job |
| 2 | A tag or commit SHA install is documented and works | CI packaging job, second variant |
| 3 | `generate_report()` returns HTML containing exactly one row per block, in definition order | Golden-file test (three-block fixture) |
| 4 | Row order is identical whether the first block or the last block finishes first | Ordering test with inverted completion order |
| 5 | A block that raises produces an error cell; the other rows still render; the document is still written | Three blocks, one failing |
| 6 | A block exceeding its timeout is marked `TIMED_OUT` and the run completes | Slow fake block |
| 7 | Three attempts with increasing backoff occur for a transient error; **zero** attempts are retried for a permanent one | Fake clock assertions on attempt count and delays |
| 8 | A 503 from the injected client is retried; a 404 is not | Normative error-mapping tests, status by status |
| 9 | Every block logs `run_id`, `block`, `status` and `duration_ms` | Captured log records, parsed as JSON |
| 10 | The document loads no external resource, while permitting anchors to external URLs | Containment test, one fixture with an anchor and one without |
| 11 | Figures render as base64 PNG data URIs and no figures leak | Figure smoke test plus `plt.get_fignums()` empty |
| 12 | `run_discovery` persists events; an identical second run reports them as duplicates, not new | SQLite round-trip with a shared behaviour suite |
| 13 | `run_processing` builds a report per pending event via the registry and marks it `PROCESSED` with its URI | Fake store plus local writer |
| 14 | Re-running processing performs no work for already-processed events | Same test, second call returns zero outcomes |
| 15 | A missing `ReportFactory` leaves the event `NEW` and records a `ConfigurationError` | Unregistered event type |
| 16 | A report that fails to upload is not marked `PROCESSED` | Writer raising `OutputError` |
| 17 | An event that fails `AUTO_INFORM_EVENT_MAX_ATTEMPTS` times is retired and never returned by `pending()` again | Store behaviour suite, boundary at exactly `max_attempts` |
| 18 | A permanently failing event does not starve later events behind it in the queue | Two events, the older permanently failing, `limit=1` |
| 19 | With `AUTO_INFORM_MAX_CONCURRENT_REPORTS > 1`, store methods are only ever called from the main thread | Store spy recording calling thread |
| 20 | `Settings` attribute names match the documented env-var mapping, and secrets never appear in `repr()` | Mapping test plus redaction test |
| 21 | `mypy` (strict via config) and `ruff` pass, and `uv lock --check` confirms the lockfile is in sync | CI |
| 22 | Postgres and S3 paths are exercised without live services | Injected stub clients; Postgres suite self-skips without a DSN |

## 4. Test naming

House style, JUnit-derived, setup and expected outcome both named:

```python
def collect_blocks_with_one_failing_block_should_isolate_failure_and_preserve_order() -> None: ...
def event_store_save_with_already_processed_event_should_not_reset_status() -> None: ...
def slug_title_with_path_separators_should_replace_with_hyphens() -> None: ...
def http_client_with_503_response_should_raise_transient_block_error() -> None: ...
```

Every test body is split into `# given / # when / # then` sections with meaningful comments. A test
whose name does not state the expected outcome is rejected in review, because six months later the
name is the only documentation anyone reads.

## 5. CI

A single GitHub Actions workflow, using uv for everything:

```bash
uv sync --frozen --all-extras   # install the pinned environment, including both extras
uv lock --check                 # fail if pyproject.toml and uv.lock have drifted
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

One service: the checks job starts PostgreSQL 17 for the optional store suite, which self-skips without the DSN it exports. No secrets, and no network beyond fetching the pinned packages once.

`--all-extras` is required rather than cosmetic. `uv lock` resolves optional extras, but
`uv sync` does not install them unless asked, so without this flag `psycopg` and `boto3` are missing
from the CI environment and criterion 22 — which exercises both code paths against stubs and a
self-skipping database suite — could never pass.

> **Scope caveat, carried from the original specification:** CI setup was **recommended, not
> mandated** as a v1 requirement by the product owner. A workflow now ships at
> `.github/workflows/ci.yml` — the checks above, plus a packaging job that runs
> `scripts/check_install.sh` — but it is convenience, not a prerequisite: every acceptance criterion in
> §3 remains verifiable without it, and a reviewer can run the same commands locally.

Test count is not a goal and coverage percentage is not enforced in v1. The acceptance criteria above
are the goal; a criterion is only satisfied when a test that would fail without the behaviour exists.

## Open questions

None. Every criterion here is either currently achievable or is an explicit fix carried in
[11-implementation-plan.md](11-implementation-plan.md).

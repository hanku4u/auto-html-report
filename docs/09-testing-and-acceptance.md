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
| `collect_blocks` | Fake clock and fake callables. Assert: ordering preserved when the first block is slowest; one failure does not prevent others running; retry count and backoff respected; timeout yields `TIMED_OUT` without aborting the run. |
| HTTP error mapping | The normative table in [02 §5.1](02-block-authoring.md#51-normative-error-mapping) is tested status by status against the client: a 503 raises a retryable error, a 404 does not, a 429 is retried, a malformed body is permanent. |
| Runners | `FakeEventStore` (in-memory dict, same semantics as the SQL schema). Assert re-discovery does not reset `PROCESSED`; a failed write leaves the event `NEW`; a missing factory skips rather than consumes; report concurrency above 1 never writes to the store off the main thread. |
| Stores | Integration tests against a temporary SQLite file. One shared behaviour suite runs against both implementations. PostgreSQL tests are marked `@pytest.mark.postgres` and skipped unless `AUTO_INFORM_TEST_POSTGRES_DSN` is set. |
| Writers | Local writer: temp dir, atomic rename, directory creation. S3 writer: injected stub client, key composition, content type, encryption header. No live AWS. |
| Rendering | Golden-file tests for the full document with a two-block fixture report. Normalise insignificant whitespace before comparing, **and normalise the footer's version token**, so that cutting a release does not break the golden files. A comparison that gets loosened every release ends up asserting nothing. This is the output contract in [06](06-output-html.md) made executable. |
| Figures | Smoke test only: build a figure, assert the URI starts `data:image/png;base64,`, and assert no figures remain open afterwards. Never assert on pixels. |
| Thread safety | Sixteen blocks in parallel, half of which render figures: no exception, stable row order. This is the test that catches a missing figure lock. |
| Configuration | Explicit `Settings` mappings, never `os.environ`. The env-var to attribute map in [07 §1.1](07-configuration-and-logging.md#11-environment-variable-to-settings-attribute-map) is asserted, so the table cannot drift from the code. |

## 2. Hermeticity rules

These are hard requirements, not preferences. A test that breaks them is a defect.

1. **No network access.** Every HTTP interaction goes through a fake or `StubHttpClient`. A test
   that needs a live API cannot run in CI and must not exist.
2. **No real wall-clock timing.** Timeouts and retry backoff are tested against an injected clock
   and a fake sleep. Asserting that something took at least 30 seconds is not a test, it is a
   delay.
3. **No live external services.** PostgreSQL is the one exception, and it self-skips without
   `AUTO_INFORM_TEST_POSTGRES_DSN`.
4. **No dependence on test order.** In particular, registry tests must not depend on whether
   `import_builtin_blocks()` has already run in the same process.
5. **Deterministic output.** `run_id` and timestamps are injected in tests, so golden files can be
   compared byte for byte.

## 3. Acceptance criteria

Each criterion must be verifiable from the repository alone, by a reviewer who did not write the
code.

| # | Criterion | Verified by |
| --- | --- | --- |
| 1 | `uv add "auto_inform @ git+…"` succeeds in a clean environment and `import auto_inform` works | CI packaging job |
| 2 | A tag or commit SHA install is documented and works | CI packaging job, second variant |
| 3 | `generate_report()` returns HTML containing exactly one row per block, in definition order | Golden-file test, two blocks |
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

No services, no secrets, no network beyond fetching the pinned packages once.

`--all-extras` is required rather than cosmetic. `uv lock` resolves optional extras, but
`uv sync` does not install them unless asked, so without this flag `psycopg` and `boto3` are missing
from the CI environment and criterion 22 — which exercises both code paths against stubs and a
self-skipping database suite — could never pass.

> **Scope caveat, carried from the original specification:** CI setup was **recommended, not
> mandated** as a v1 requirement by the product owner. The workflow above is the recommended shape,
> and every acceptance criterion in §3 is written to be verifiable without it — a reviewer can run
> the same four commands locally. Adopting CI is a separate decision, not a prerequisite for
> declaring v1 done.

Test count is not a goal and coverage percentage is not enforced in v1. The acceptance criteria above
are the goal; a criterion is only satisfied when a test that would fail without the behaviour exists.

## Open questions

None. Every criterion here is either currently achievable or is an explicit fix carried in
[11-implementation-plan.md](11-implementation-plan.md).

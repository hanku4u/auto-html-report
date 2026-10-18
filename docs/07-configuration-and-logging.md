---
status: proposed
description: Environment variables and their Settings attributes, the context factories, the HTTP stub, and the structured logging contract.
replaces: docs/SPEC.md §7.1, §7.2, §7.3
---

# 07 — Configuration, stubs and logging

## 1. Where configuration comes from

Environment variables only, parsed once at process start into a frozen `Settings` object. There is
no config file and no CLI in v1 (ADR-0014): this runs as a CI job, and CI already has an
environment. Tests construct `Settings` directly and never touch `os.environ`.

### 1.1 Environment variable to `Settings` attribute map

The attribute names are part of the contract, because blocks read them — for example
`context.settings.block_timeout_seconds` is passed as an HTTP `timeout` in
[02](02-block-authoring.md#7-worked-example). An implementation that invents different names breaks
every block.

| Environment variable | `Settings` attribute | Type | Default |
| --- | --- | --- | --- |
| `AUTO_INFORM_OUTPUT_KIND` | `output_kind` | `Literal["local","s3"]` | `"local"` |
| `AUTO_INFORM_OUTPUT_DIR` | `output_dir` | `Path` | `./reports` |
| `AUTO_INFORM_OUTPUT_S3_BUCKET` | `output_s3_bucket` | `str \| None` | `None` |
| `AUTO_INFORM_OUTPUT_S3_PREFIX` | `output_s3_prefix` | `str` | `""` |
| `AUTO_INFORM_OUTPUT_S3_SSE` | `output_s3_sse` | `str \| None` | `None` |
| `AUTO_INFORM_EVENT_STORE` | `event_store` | `Literal["sqlite","postgres"]` | `"sqlite"` |
| `AUTO_INFORM_SQLITE_PATH` | `sqlite_path` | `Path` | `<output_dir>/events.sqlite3` |
| `AUTO_INFORM_POSTGRES_DSN` | `postgres_dsn` | `str \| None` | `None` |
| `AUTO_INFORM_EVENT_MAX_ATTEMPTS` | `event_max_attempts` | `int` | `3` |
| `AUTO_INFORM_MAX_WORKERS` | `max_workers` | `int` | `8` |
| `AUTO_INFORM_MAX_CONCURRENT_REPORTS` | `max_concurrent_reports` | `int` | `1` |
| `AUTO_INFORM_PROCESSING_LIMIT` | `processing_limit` | `int` | `500` |
| `AUTO_INFORM_BLOCK_TIMEOUT_SECONDS` | `block_timeout_seconds` | `float` | `60.0` |
| `AUTO_INFORM_BLOCK_RETRIES` | `block_retries` | `int` | `2` |
| `AUTO_INFORM_RETRY_BASE_DELAY_SECONDS` | `retry_base_delay_seconds` | `float` | `1.0` |
| `AUTO_INFORM_RETRYABLE_EXCEPTIONS` | `retryable_exceptions` | `tuple[str, ...]` | `()` |
| `AUTO_INFORM_LOG_LEVEL` | `log_level` | `Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"]` | `"INFO"` |
| `AUTO_INFORM_LOG_FORMAT` | `log_format` | `Literal["json","console"]` | `"json"` |
| `AUTO_INFORM_ERROR_DETAIL` | `error_detail` | `Literal["summary","detailed"]` | `"summary"` |
| `AUTO_INFORM_FAIL_ON_BLOCK_ERROR` | `fail_on_block_error` | `bool` | `True` |
| `AUTO_INFORM_HTTP_BASE_URL` | `http_base_url` | `str \| None` | `None` |
| `AUTO_INFORM_HTTP_API_TOKEN` | `http_api_token` | `str \| None` | `None` |
| `AUTO_INFORM_HTTP_MAX_REDIRECTS` | `http_max_redirects` | `int` | `5` |

Two integers have an upper bound as well as a floor, because the executor's arithmetic cannot survive
them: `AUTO_INFORM_BLOCK_RETRIES` is capped at **10** — the backoff doubles per attempt, so a larger
count overflows the hang-guard computation before any work starts — and `AUTO_INFORM_EVENT_MAX_ATTEMPTS`
at **1000**, beyond which the value cannot be bound to SQLite's `INTEGER`. A block's own `max_retries`
override is subject to the same cap, and the executor enforces both on a directly constructed
`Settings`, because `load_settings` is not the only way to build one. Concurrency is capped too, because
it multiplies: `AUTO_INFORM_MAX_WORKERS` at **64**, `AUTO_INFORM_MAX_CONCURRENT_REPORTS` at **32** (each
report starts its own block pool), and `AUTO_INFORM_PROCESSING_LIMIT` at **10,000**.

Rules for `load_settings`:

- **Report every invalid variable, not just the first.** A CI job that fails four times to reveal
  four typos is a bad job. Collect all failures and raise one `ConfigurationError` listing them.
- Validate cross-field requirements at load time: `output_kind="s3"` requires `output_s3_bucket`;
  `event_store="postgres"` requires `postgres_dsn`. Both raise `ConfigurationError` immediately,
  not at first use.
- Reject non-positive values where zero is meaningless — timeouts, workers, concurrent reports, attempt
  ceilings — and negative values everywhere, including retry counts. A zero timeout or zero workers is a
  configuration bug, not a request for unlimited throughput; a retry count of zero, by contrast, is a
  legitimate preference.
- Reject non-finite numbers. `nan` and `inf` pass every `<` comparison, and both are dangerous rather than
  merely odd: a NaN budget disables the per-attempt timeout (`consumed > nan` is never true), and an
  infinite one makes the hang guard unreachable.
- Enum-like values (`output_kind`, `event_store`, `log_format`, `error_detail`, `log_level`) are matched
  **case-insensitively** and stored canonically. `log_level` is validated against the standard names here
  rather than left to `logging.setLevel`, which would raise a raw `ValueError` from another module and
  never appear in this loader's collected report.

> **Secrets are never logged.** `Settings.__repr__` redacts any attribute whose name contains
> `TOKEN`, `SECRET`, `PASSWORD`, `DSN` or `KEY` — because the first debugging session in production
> will `log.info("settings=%s", settings)`, and that must be boring.

## 2. The HTTP stub

Every external call is a stub in v1. Blocks and discoverers are written against the port, so
swapping in a real client later is a one-line wiring change.

```python
# auto_inform/infrastructure/http_stub.py
class StubHttpClient:
    """Deterministic stand-in for the real API client.

    Fixtures are keyed by path, so tests and demo reports are reproducible and CI
    needs no network. Raises StubNotConfiguredError for an unknown path: a loud
    failure beats a silently empty report.
    """

    def __init__(self, fixtures: Mapping[str, Any] | None = None) -> None: ...

    def get_json(self, path: str, *, params=None, timeout: float, headers=None) -> Any: ...
    def get_bytes(self, path: str, *, params=None, timeout: float, headers=None) -> bytes: ...
```

The stub enforces the two rules that would otherwise make a real client a bad retrofit:

1. **`timeout` is a required keyword** on every method, exactly as in the port
   ([02 §5](02-block-authoring.md#5-the-httpclient-port)).
2. **One instance is shared across threads**, so it must be thread-safe — the stub says so, and a
   test hammers it from multiple threads.

The stub raises the same framework exceptions the normative error mapping prescribes for a real
client, so tests can exercise retry and error-cell behaviour without a server. Use
`StubHttpClient.raising_for(path, TransientBlockError(...))` to simulate a 503.

## 3. Context factories

Five helpers form the seam between ambient environment and explicitly injected dependencies. They
read the environment exactly once, at the edge, and hand the application layer pure data.

```python
def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Parse and validate environment variables into a frozen Settings.

    Accepts an explicit mapping so tests never touch os.environ.
    """

def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the structured handler on the root logger. Idempotent."""

def new_report_context(settings: Settings) -> ReportContext:
    """Generate a fresh run_id and started_at; wire http/logger/clock from settings."""

def new_discovery_context(settings: Settings) -> DiscoveryContext:
    """As above, for the discovery phase."""

def build_output_writer(settings: Settings) -> OutputWriter:
    """Return the writer selected by AUTO_INFORM_OUTPUT_KIND."""
```

Each returns a value rather than registering a global, so a long-lived process — a notebook, a test
session — can build several contexts with different settings without them contaminating each other.
`run_id` is a fresh `uuid4().hex` per context, which is what makes a report traceable back to the
log lines that produced it.

**Where they live.** `load_settings` is in `infrastructure/settings.py`, `configure_logging` in
`logging_setup.py`, and the three context/writer factories in `infrastructure/factories.py` — the one
module allowed to name concrete adapters, because that is what wiring is.

**Additive overrides.** `new_report_context` and `new_discovery_context` also accept optional keyword
arguments for `http`, `clock` and `logger`. The documented call — `new_report_context(settings)` — still
works unchanged; the overrides exist so a test or a demo job can supply canned fixtures and a
deterministic clock without building a context by hand.

**The S3 gap is loud.** `build_output_writer` selects the local writer, and for
`AUTO_INFORM_OUTPUT_KIND=s3` it raises `ConfigurationError` rather than quietly writing to the
filesystem: a job that believes it is uploading and is not is worse than one that will not start. The
real branch arrives with stage 11.

## 4. Logging contract

`configure_logging` installs a JSON formatter (stdlib `logging` only — no extra dependency) with
one line per notable event. Every line carries `run_id`; every block line carries `block`,
`report_title`, and `event_id` where applicable.

| Event | Fields |
| --- | --- |
| `report.start` | `run_id`, `report_title`, `block_count` |
| `block.start` | `run_id`, `block`, `attempt` |
| `block.retry` | `run_id`, `block`, `attempt`, `delay_s`, `error_type` |
| `block.finish` | `run_id`, `block`, `status`, `duration_ms`, `attempts` |
| `block.failed` | as above, plus `error_type`, `error_message`, and the traceback |
| `block.abandoned` | `run_id`, `block`, `overrun_s` — a timed-out worker thread that outlived its budget |
| `report.finish` | `run_id`, `report_title`, `failed_blocks`, `duration_ms`, `output_uri` |
| `report.failed` | `run_id`, `report_title`, `error_type`, and the traceback — the report could not be completed: a refused definition, a renderer failure, or a failed write. When the document was produced and only the write failed, it is followed by `report.finish` with `output_uri: null`; otherwise — a refused definition or a renderer failure — it is the terminal line for the pair. |
| `discovery.start` | `run_id`, `discoverer`, `attempt` |
| `discovery.retry` | `run_id`, `discoverer`, `attempt`, `delay_s`, `error_type` |
| `discovery.finish` | `run_id`, `discoverer`, `event_type`, `new`, `duplicates`, `duration_ms` — emitted after the events are persisted, and only on the success path |
| `discovery.failed` | `run_id`, `discoverer`, `attempts`, `error_type`, `error_message`, and the traceback — the discoverer itself failed, so nothing from it was persisted |
| `discovery.abandoned` | `run_id`, `discoverer`, `overrun_s` — a timed-out discoverer thread that outlived its budget |
| `processing.capped` | `run_id`, `requested`, `limit` — a caller asked for more events than `AUTO_INFORM_PROCESSING_LIMIT` allows, and the configured ceiling won |
| `event.processed` | `run_id`, `event_type`, `event_id`, `output_uri` |
| `event.failed` | `run_id`, `event_type`, `event_id`, `attempts`, `reason` |
| `event.retired` | `run_id`, `event_type`, `event_id`, `attempts` — attempts exhausted, no longer retried |
| `event.skipped` | `run_id`, `event_type`, `event_id`, `error_type`, and the traceback — the event was left `NEW` because the deployment is wrong (a missing factory, a refused definition). No attempt was counted, which is why this is not `event.failed`. |
| `store.failed` | `run_id`, `event_type`, `event_id`, and the traceback — a store call failed during the commit phase; the run stops after recording it |

`duration_ms` on every block is the point: when the nightly job goes from 40 seconds to 9 minutes,
the log says which block got slower, without a profiler or a reproduction. `block.abandoned` exists
because a timed-out thread is not stoppable ([02 §6.2](02-block-authoring.md#62-timeout-accounting))
and silently abandoning it would hide a real, growing problem.

`AUTO_INFORM_LOG_FORMAT=console` renders a human-readable single line instead, for local debugging.
It is not the CI default because nobody wants to parse a coloured traceback out of a build log.

## Testing

- `load_settings` is tested with explicit mappings, never via `os.environ`: one test per invalid
  variable, one test asserting that *three* simultaneous errors are all reported in a single
  exception, one per cross-field requirement, and one per numeric bound.
- Redaction: `repr(settings)` and `str(settings)` must not contain the value of a token or DSN.
- Attribute names: a test asserting the full env-var → attribute mapping in §1.1 holds, so the table
  cannot silently drift from the code.
- Logging: capture records and assert the JSON body of a `block.finish` line contains `run_id`,
  `block`, `status` and `duration_ms`. `configure_logging` is idempotent under repeated calls.
- The stub: fixtures by path, an unknown path raising `StubNotConfiguredError` (a `ConfigurationError`, so
  it is catchable as an `AutoInformError`), `get_bytes` refusing a non-bytes fixture, `raising_for`, and
  eight threads sharing one instance with no exception and no fixture corruption.

## Open questions

| # | Question | v1 default |
| --- | --- | --- |
| 1 | Should S3 server-side encryption default to on rather than opt-in, given that reports can contain yield and maintenance data? | Keep opt-in in v1 (`AUTO_INFORM_OUTPUT_S3_SSE` unset means no SSE header), but flag it for the security review before the first production bucket is created. The bucket policy is usually the right place for this, not the client. |

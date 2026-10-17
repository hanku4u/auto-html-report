---
status: proposed
description: The Block contract, its injected context, the HTTP port and its error mapping, and the per-block execution rules for a single report.
replaces: docs/SPEC.md §4.2-4.3, §5, §7.2
---

# 02 — Block authoring and execution

This document is the contract a block author writes against. It also defines what the framework
does *to* a block: timeout, retry, failure isolation and ordering.

## 1. The Block contract

```python
# auto_inform/domain/block.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

DataT = TypeVar("DataT")


class Block(ABC, Generic[DataT]):
    """One row of a report.

    Subclasses split the work in two so that rendering stays trivially testable:
      collect() -> I/O, allowed to be slow and to fail
      render()  -> pure transformation, no I/O, no clock, no randomness
    """

    #: Default text for the "Item" column. Overridable per instance via ``name=``.
    display_name: ClassVar[str] = ""

    #: Per-block overrides; ``None`` means "use the framework default from Settings".
    timeout_seconds: ClassVar[float | None] = None
    max_retries: ClassVar[int | None] = None

    def __init__(self, *, name: str | None = None) -> None:
        resolved = name or self.display_name
        if not resolved:
            raise ValueError(
                f"{type(self).__name__} must set display_name or pass name=..."
            )
        self._name = resolved

    @property
    def name(self) -> str:
        """Text shown in the Item column. Must be unique within a report."""
        return self._name

    @abstractmethod
    def collect(self, context: ReportContext) -> DataT:
        """Gather this block's data. Runs in a worker thread; I/O permitted."""

    @abstractmethod
    def render(self, data: DataT, context: RenderContext) -> str:
        """Return the inner HTML for this block's Data cell. Pure."""
```

**Why `name` is a property and not a bare class attribute.** The same block type is frequently
instantiated several times in one report with different parameters — `Maintenance — T-104` versus
`Maintenance — T-112`. `display_name` keeps the simple case a one-liner; `name=` handles the
repeated case. Names must be unique within a report; `ReportDefinition` rejects duplicates at
construction (see [03](03-report-api.md)).

**`DataT` is opaque to the framework.** It is typed end to end for the author's benefit only. The
framework never inspects it, serialises it, or passes it between blocks.

The two context types appearing in those signatures — `ReportContext` and `RenderContext` — are
defined in [03 §5](03-report-api.md#5-the-injected-contexts), along with the thread-safety rules for
sharing them.

**Blocks do not talk to each other.** If two blocks need the same data, they fetch it themselves,
or a `ReportFactory` fetches it once and injects it as a constructor argument. That second option is
the sanctioned escape hatch and is why insertion order is never a data-dependency mechanism.

## 2. `collect()` — the I/O phase

- Runs in a worker thread. It must be safe to call off the main thread.
- May perform network, filesystem or database I/O. May be slow. May fail.
- **Must honour its timeout.** The framework cannot interrupt a running thread, so a block that
  ignores `timeout=` on its HTTP calls will outlive its own budget. See
  [§6.2 Timeout accounting](#62-timeout-accounting).
- **Figure creation belongs in `render()`, and it must be wrapped in `figure_lock()`.** Matplotlib is
  not thread-safe, and the framework can only serialise the matplotlib work it performs itself — the
  save. Creating a figure happens in the block's own code, so the block holds the lock while it
  creates, draws, saves and closes. See [06](06-output-html.md#5-figures) and ADR-0025.
- Should raise `TransientBlockError` or `PermanentBlockError` (below) to control whether the
  framework retries it. Any other exception is treated as permanent.

## 3. `render()` — the pure phase

- **Pure**: no I/O, no clock, no randomness, no logging, no mutation of inputs.
- Must not raise. The framework wraps it defensively and substitutes an error cell if it does, but
  a render bug is a code defect, not a data condition, and belongs in a unit test.
- Receives the value its own `collect()` returned.
- Returns the **inner HTML** of the Data cell — not a `<td>`, not a row, not a document.
- Is responsible for escaping anything it interpolates from outside the process. This is the single
  most important rule in the document set; see [06](06-output-html.md#4-the-trust-boundary).

Both phases must be safe for **one block instance to be used by exactly one thread at a time**.
The framework guarantees that: instances are collected and rendered sequentially within their own
worker. Per-instance mutable state is therefore fine; cross-instance state is not.

## 4. The exception hierarchy

```python
# auto_inform/domain/errors.py
class AutoInformError(Exception): ...
class BlockError(AutoInformError): ...
class TransientBlockError(BlockError): ...      # retryable: 5xx, 429, connection reset
class PermanentBlockError(BlockError): ...      # not retryable: 404, bad schema, auth failure
class ConfigurationError(AutoInformError): ...  # bad env, missing factory, duplicate block name
class StoreError(AutoInformError): ...          # event store unreachable or corrupt
class OutputError(AutoInformError): ...         # report could not be written
```

Blocks may raise these directly. More commonly, the injected HTTP client raises them on the
author's behalf — see below.

## 5. The `HttpClient` port

Blocks never build their own HTTP client and never read the environment. They receive one through
the context:

```python
# auto_inform/application/ports.py
class HttpClient(Protocol):
    """Injected HTTP access. Implementations MUST be thread-safe: one instance is
    shared by every block in a report, and blocks run concurrently."""

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,                        # required: the framework cannot kill a thread
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...

    def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> bytes: ...
```

`path` may be absolute or relative to `AUTO_INFORM_HTTP_BASE_URL`. Authentication is the client's
concern: the injected implementation attaches the bearer token from settings. Blocks must never see
or log credentials.

### 5.1 Normative error mapping

This table is the contract. An implementation that maps status codes differently will silently
change retry behaviour, so it is normative rather than advisory.

| Outcome | Raised as | Retried by the framework |
| --- | --- | --- |
| 2xx with a parsable body | returns the parsed value | — |
| 2xx with an unparsable body | `PermanentBlockError` | no |
| 408, 425, 429 | `TransientBlockError` | **yes** |
| 500, 502, 503, 504 | `TransientBlockError` | **yes** |
| 400, 401, 403, 404, 409, 422 | `PermanentBlockError` | no |
| Other 4xx | `PermanentBlockError` | no |
| Connection timeout or read timeout within `timeout` | `TimeoutError` | **yes** |
| DNS failure, connection refused or reset | `ConnectionError` | **yes** |
| 3xx | followed by the client, up to 5 hops; the final status decides | per final status |
| TLS failure | `PermanentBlockError` | no |

> **Why this table exists.** An earlier draft of this specification said "5xx is retryable" in a
> comment while listing only `TimeoutError` and `ConnectionError` as retryable. A 503 surfacing as
> a library-specific `HTTPError` would therefore have been classified as permanent and never
> retried — the exact opposite of the intent. Mapping happens **inside the client**, so that block
> authors never have to know which HTTP library is underneath.

## 6. Execution semantics for one report

### 6.1 Concurrency

One `ThreadPoolExecutor` per report, sized `min(settings.max_workers, len(blocks))`. Every block is
submitted immediately; results are gathered as they complete.

Reports themselves may also run concurrently — that is a property of the *processing job*, not of
`generate_report`, and is specified in [05](05-discovery-and-processing.md#4-throughput-policy).

### 6.2 Timeout accounting

The naive implementation is wrong, so the rule is explicit:

- The retry loop **and** the timeout check both live **inside** the worker function. The timeout
  therefore covers `collect()` itself, not time spent queued behind other blocks.
- The calling thread reads `future.result(...)` with an outer backstop of
  `attempt_timeout * total_attempts + sum(backoffs) + grace`. Hitting it yields `TIMED_OUT` rather
  than an exception escaping the pipeline.
- Both layers of enforcement are driven by the injected `Clock`: budgets are measured with
  `monotonic()` and every wait goes through `sleep()`. That is what allows a fake clock to produce a
  deterministic `TIMED_OUT` with no real waiting — see [03 §5](03-report-api.md#5-the-injected-contexts).

> **Gotcha — Python cannot kill a thread.** A block that overruns is *abandoned*: the future is
> marked failed, the run continues, and the orphaned thread keeps running until its own I/O call
> returns. This is precisely why `timeout` is a required keyword on every `HttpClient` method.
> Abandoned threads are logged at `WARNING` with the block name and the overrun duration, so a
> persistently slow block is visible rather than merely tolerated.

`ProcessPoolExecutor` was rejected as the alternative (see ADR-0005): it would force every block
payload to be picklable and drag matplotlib into subprocess lifecycle management.

### 6.3 Retry classification

| Setting | Meaning | Default |
| --- | --- | --- |
| `AUTO_INFORM_MAX_WORKERS` | Ceiling on in-flight blocks per report | `8` |
| `AUTO_INFORM_BLOCK_TIMEOUT_SECONDS` | Per-block wall-clock budget for **one attempt** | `60` |
| `AUTO_INFORM_BLOCK_RETRIES` | Retries **after** the first attempt | `2` |
| `AUTO_INFORM_RETRY_BASE_DELAY_SECONDS` | Backoff base: `base * 2**attempt + jitter(0..base)` | `1.0` |
| `AUTO_INFORM_RETRYABLE_EXCEPTIONS` | Extra retryable exception class names, comma-separated | `""` |

The default retryable set is `TransientBlockError`, `TimeoutError`, `ConnectionError`, plus any
types named in `AUTO_INFORM_RETRYABLE_EXCEPTIONS`. **Every other exception is treated as
permanent**, including `KeyError` from a malformed API payload — it will not improve on the second
attempt. Retrying a `404` three times is just a slower way to fail.

A block overrides the policy per instance by setting `timeout_seconds` or `max_retries`, or per
call by raising a different exception type.

### 6.4 Failure isolation

A block failure produces a `FAILED` or `TIMED_OUT` outcome and an error cell; it never propagates.
This is a hard requirement, not a policy: a report where one of twelve blocks is red is still a
useful report, and "no output at all because the maintenance API was briefly down" is precisely the
failure mode this framework exists to prevent.

Exceptions that **do** abort a run: `ConfigurationError` during setup (invalid definition, missing
factory, unreadable settings) and `StoreError` / `OutputError` at the boundaries. Those are operator
problems, not data problems, and failing loudly is correct.

### 6.5 Deterministic ordering

Rows appear in `ReportDefinition.blocks` order — always. Internally, results arrive from
`as_completed` and are written into a pre-sized array by index; the array is read in order at the
end. This is the whole reason collection returns an ordered `tuple` rather than yielding as it
finishes. A report containing one slow and one fast block must produce identical row order
regardless of completion order, and that is an acceptance criterion in
[09](09-testing-and-acceptance.md).

## 7. Worked example

```python
# auto_inform/blocks/maintenance_history.py
from __future__ import annotations

from dataclasses import dataclass

from auto_inform import Block, ReportContext, RenderContext, TransientBlockError, html_table


@dataclass(frozen=True, slots=True)
class MaintenanceRecord:
    performed_at: str
    engineer: str
    summary: str


class MaintenanceHistoryBlock(Block[list[MaintenanceRecord]]):
    """Recent maintenance events for one tool."""

    display_name = "Maintenance history"
    timeout_seconds = 30.0

    def __init__(self, *, tool_id: str, days: int = 30, name: str | None = None) -> None:
        super().__init__(name=name or f"Maintenance history — {tool_id}")
        self._tool_id = tool_id
        self._days = days

    def collect(self, context: ReportContext) -> list[MaintenanceRecord]:
        payload = context.http.get_json(
            f"/maintenance/{self._tool_id}",
            params={"days": self._days},
            timeout=context.settings.block_timeout_seconds,
        )
        if payload.get("status") == "degraded":
            # The transport succeeded; the service is degraded. Retryable by our own judgement.
            raise TransientBlockError("maintenance service degraded; retry later")
        return [MaintenanceRecord(**row) for row in payload["records"]]

    def render(self, data: list[MaintenanceRecord], context: RenderContext) -> str:
        if not data:
            return '<p class="ai-empty">No maintenance recorded in this window.</p>'
        return html_table(
            headers=["Date", "Engineer", "Summary"],
            rows=[(r.performed_at, r.engineer, r.summary) for r in data],
        )
```

Note what the author did **not** write: no `requests.get`, no `os.environ`, no retry loop, no
`logging.getLogger` boilerplate, and no string concatenation of external data into HTML.
`html_table()` escapes every cell it is given.

A figure-producing block is in [06](06-output-html.md#5-figures).

## 8. Block author's checklist

- [ ] `collect()` declares what it needs as constructor parameters, not by reading ambient state.
- [ ] Every HTTP call passes a `timeout`.
- [ ] Any external string rendered into HTML goes through `escape()` or `html_table()`.
- [ ] `render()` touches no network, no clock, no filesystem, and no logger.
- [ ] Failures are raised as `TransientBlockError` / `PermanentBlockError` when the default
      classification would be wrong.
- [ ] `name` is unique within every report the block is used in.
- [ ] Figures, if any, are created in `render()`, and the `finally: plt.close(fig)` is present.

## Testing

- `render()` is a pure function: test it directly with hand-built data, no fixtures, no mocks.
- `collect()` is tested against a fake `ReportContext` (`FakeHttpClient`, frozen clock) — never
  against a live API. See [09](09-testing-and-acceptance.md).
- The execution rules in §6 are tested in `tests/unit/test_collect_blocks.py`: ordering under
  completion-order inversion, retry counts and delays against a fake clock, timeout producing
  `TIMED_OUT` without aborting the run, and one failing block not preventing its peers.

## Dev plan

Implemented across stages 2, 4 and 5 of [11-implementation-plan.md](11-implementation-plan.md): the
domain types in stage 2, the registry and first blocks in stage 4, and the concurrency, timeout,
retry and ordering rules of §6 in stage 5. Stage 5 is deliberately the highest-risk review boundary
in the project.

## Open questions

| # | Question | v1 default |
| --- | --- | --- |
| 1 | Should a block be able to declare a per-attempt timeout distinct from its overall budget (e.g. "3 attempts, 10 s each")? | No. One budget per attempt plus `max_retries`; revisit if a real block needs asymmetry. |

---
status: proposed
description: The two scheduled jobs — the Discoverer port, run_discovery, run_processing, throughput policy, exit codes and worked job scripts.
replaces: docs/SPEC.md §4.8, §4.9, §7.4, §10
---

# 05 — Discovery and processing jobs

Two independently scheduled jobs share only the event store
([04](04-events-and-stores.md)). Either can fail, be re-run, be throttled, or run on a different
cadence without affecting the other. See ADR-0008.

## 1. Discovery

```python
# auto_inform/application/discovery.py
class Discoverer(ABC):
    """Produces events of exactly one type from one external source."""

    event_type: ClassVar[str]

    @abstractmethod
    def discover(self, context: DiscoveryContext) -> Iterable[Event]:
        """Return the events currently visible at the source. May perform I/O."""
```

```python
@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    discoverer: str                   # class name
    event_type: str
    discovered: int
    new: int
    duplicates: int
    duration_seconds: float
    error_type: str | None = None     # set when the discoverer itself failed
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    run_id: str
    outcomes: tuple[DiscoveryOutcome, ...]
    started_at: datetime
    finished_at: datetime

    @property
    def all_succeeded(self) -> bool: ...


def run_discovery(
    discoverers: Sequence[Discoverer],
    *,
    store: EventStore,
    context: DiscoveryContext,
) -> DiscoveryRun:
    """Run every discoverer concurrently; persist what they found.

    A discoverer that raises is recorded in its DiscoveryOutcome, not re-raised:
    one broken source must not prevent the others from being persisted.
    """
```

Discoverers are subject to the same timeout, retry and failure-isolation rules as blocks — see
[02 §6](02-block-authoring.md#6-execution-semantics-for-one-report). Those mechanics are implemented
**once**, in `application/collect_blocks.py`, parameterised by the callable being run. Discoverers
and blocks therefore cannot drift apart in behaviour, which is the point.

`DiscoveryContext` carries `settings`, `http`, `logger`, `clock` and `run` — the same shape as
`ReportContext` minus nothing, and equally frozen and thread-safe.

## 2. Processing

```python
@dataclass(frozen=True, slots=True)
class EventProcessingOutcome:
    event: Event
    report_title: str
    rows: tuple[BlockOutcome, ...]
    output_uri: str | None
    error_type: str | None = None     # set when the whole event failed to process
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    run_id: str
    outcomes: tuple[EventProcessingOutcome, ...]
    started_at: datetime
    finished_at: datetime

    @property
    def all_succeeded(self) -> bool: ...


def run_processing(
    *,
    store: EventStore,
    context: ReportContext,
    writer: OutputWriter,
    event_type: str | None = None,      # restrict to one type; None = all registered types
    limit: int | None = None,
    fail_events_with_failed_blocks: bool = False,
) -> ProcessingRun:
    """Fetch pending events, build each event's report, write it, and update the store."""
```

Behavioural rules, each of which has a test in [09](09-testing-and-acceptance.md):

1. **The factory supplies everything event-specific.** For each event,
   `get_report_factory(event.event_type)(event, context)` produces the `ReportDefinition`. There is
   no other path from an event to a block set.
2. **A missing factory does not consume the event.** `ConfigurationError` is recorded against that
   event, the event is left `NEW`, and the run continues with other types. A half-deployed job must
   not silently eat events it cannot render.
3. **`mark_processed` is called only after the writer returns.** A report that failed to upload must
   not be recorded as delivered, or it is lost with nobody the wiser.
4. **Failed blocks do not fail the event by default.** A report whose blocks produced error cells
   but which was written successfully is `PROCESSED`, because the error cells *are* the deliverable.
   Set `fail_events_with_failed_blocks=True` (or `AUTO_INFORM_FAIL_ON_BLOCK_ERROR`) to instead mark
   such events `FAILED` for operator follow-up.
5. **`run_processing` never raises for a data-level failure.** Invalid settings, an unreachable
   store and a failing writer are the only exceptions that escape, and they map onto exit codes
   below.

## 3. Processing order per event

```
Event ──► factory ──► ReportDefinition ──► generate_report ──► ReportResult
                                                │
                            ┌───────────────────┴────────────────────┐
                            ▼                                        ▼
                     (writer supplied)                        (audit only)
                    write HTML, get URI                    inspect outcomes
                            │
                            ▼
              store.mark_processed(event, uri)
                            │
                on failure ─┴─► store.mark_failed(event, reason)  ── attempts += 1
```

## 4. Throughput policy

`generate_report` parallelises *blocks within one report*. `run_processing` decides how many
*reports* proceed at once:

| Setting | Meaning | Default |
| --- | --- | --- |
| `AUTO_INFORM_MAX_CONCURRENT_REPORTS` | Reports processed simultaneously per run | `1` |

**The default is 1, deliberately.** With sequential events, every store call happens on the main
thread, which is what makes the single-writer assumption in [04](04-events-and-stores.md) safe and
keeps `mark_processed` trivially correct. Raising the value is supported: report building happens
in worker threads and **all store writes are marshalled back onto the main thread**, so the store is
never touched concurrently regardless of the setting.

Set it higher when a run's event count makes wall-clock time a problem. The per-report block pool
is sized `min(AUTO_INFORM_MAX_WORKERS, len(blocks))`, so raising report concurrency multiplies the
number of in-flight HTTP calls — size it against the API's rate limits, not against CPU. See
ADR-0020, and note that if a single run ever needs to process hundreds of events, sharding by
event type across several jobs is usually a better answer than one very wide job.

The store is written by one process. Concurrent job *processes* against the same store are not
supported in v1 — `pending()` and `mark_*` are not atomic across processes, so two runs can claim
the same event. Claim/lease semantics are a v0.2 concern, recorded in
[10 — Decision log](10-decision-log.md) as ADR-0007 and ADR-0020.

## 5. Exit codes for CI

There is no CLI (ADR-0001), so the framework supplies the vocabulary and the client's job script
decides when to exit.

```python
class ExitCode(IntEnum):
    SUCCESS             = 0   # every event processed, every block OK
    PARTIAL_FAILURE     = 1   # reports produced, but at least one block or event failed
    CONFIGURATION_ERROR = 2   # bad settings, invalid definition, missing factory for all events
    STORE_ERROR         = 3   # event store unreachable or corrupt
    OUTPUT_ERROR        = 4   # a report could not be written


def exit_code_for(run: DiscoveryRun | ProcessingRun) -> ExitCode:
    """Map a finished run to a process exit code. Never raises."""
```

**Precedence, when several conditions apply.** The most severe wins, in this order:

| Rank | Code | Applies when |
| --- | --- | --- |
| 1 | `CONFIGURATION_ERROR` (2) | A `ConfigurationError` escaped the run: settings unreadable, or no usable factory for any event encountered. |
| 2 | `STORE_ERROR` (3) | The store failed to open, or a store call raised `StoreError` mid-run. |
| 3 | `OUTPUT_ERROR` (4) | At least one report was built but could not be written. |
| 4 | `PARTIAL_FAILURE` (1) | At least one event failed to process, or at least one block failed while `AUTO_INFORM_FAIL_ON_BLOCK_ERROR` is true. |
| 5 | `SUCCESS` (0) | None of the above. |

Consequence: a report containing three failed blocks exits `1`, so the CI job goes amber and
somebody looks. That is intended. Operators who consider a partly-red report acceptable set
`AUTO_INFORM_FAIL_ON_BLOCK_ERROR=false` and get `0`.

## 6. Example job scripts

```python
# my_jobs/discover_wafers.py
import sys

from auto_inform import (
    DiscoveryContext, Discoverer, Event, SqliteEventStore, configure_logging,
    exit_code_for, load_settings, new_discovery_context, run_discovery,
)


class FailingWaferDiscoverer(Discoverer):
    event_type = "WAFER_FAILURE"

    def discover(self, context: DiscoveryContext):
        for lot_id in context.http.get_json("/yield/failures", timeout=30.0)["lots"]:
            yield Event(
                event_type=self.event_type,
                event_id=f"WAFER_FAILURE:{lot_id}",
                payload={"lot_id": lot_id, "tool_id": "T-104",
                         "summary": "Below yield threshold"},
                discovered_at=context.clock.now(),
                source="yield-api",
            )


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    with SqliteEventStore.from_settings(settings) as store:
        run = run_discovery(
            [FailingWaferDiscoverer()],
            store=store,
            context=new_discovery_context(settings),
        )
    return exit_code_for(run)


if __name__ == "__main__":
    sys.exit(main())
```

```python
# my_jobs/process_events.py — the whole scheduled job
import sys

from auto_inform import (
    SqliteEventStore, build_output_writer, configure_logging,
    exit_code_for, load_settings, new_report_context, run_processing,
)


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    writer = build_output_writer(settings)
    with SqliteEventStore.from_settings(settings) as store:
        run = run_processing(
            store=store,
            context=new_report_context(settings),
            writer=writer,
        )
    return exit_code_for(run)


if __name__ == "__main__":
    sys.exit(main())
```

Both scripts import `auto_inform.reports.manufacturing` (or whatever modules hold the report
factories) at the top, because a factory registers itself on import. Forgetting that import is the
one mistake these scripts make, and the failure mode is loud rather than silent: every event
records a `ConfigurationError` and exits `2`.

## 7. End-to-end walkthrough

1. **Discovery** runs on a 15-minute schedule. The discoverer asks the yield API which lots failed,
   yields one `Event` per lot, and `save()` reports 4 new and 96 duplicates — the API returns the
   same rolling window every time, which is exactly why `save()` is insert-only and idempotent.
2. **Processing** runs hourly. `pending(event_type="WAFER_FAILURE", limit=50)` returns the 4 `NEW`
   events, oldest first.
3. For each, the registered factory builds a `ReportDefinition` using the event's `lot_id` and
   `tool_id`.
4. `generate_report` collects the blocks concurrently, renders the document, and the writer puts it
   in `reports/` (or S3) and returns a `file://` or `s3://` URI.
5. `mark_processed` records the URI. The next run's `pending()` no longer returns that event.
6. If a block failed, its cell holds an error cell, the event is still `PROCESSED`, and the process
   exits `1`.

## Testing

- `FakeEventStore` (in-memory, same semantics as the SQL schema) drives all `run_*` tests. Assert:
  a failed write leaves the event `NEW`; a missing factory skips rather than consumes; re-running
  processing returns zero outcomes for already-processed events; the outcomes tuple preserves event
  order.
- A fake clock and a fake sleep make retry backoff and timeouts deterministic — no `time.sleep`, no
  real wall-clock assertions. Hermeticity is a hard requirement; see
  [09 §2](09-testing-and-acceptance.md#2-test-rules).
- `exit_code_for` is tested as a pure function over synthetic runs, one test per precedence rank
  and one for a tie between ranks.

## Dev plan

Implemented across stages 8, 9 and 10 of [11-implementation-plan.md](11-implementation-plan.md): the
SQLite store and discovery in stage 8, processing plus exit codes in stage 9, and the example job
scripts in stage 10. The two acceptance criteria that specifically guard this document — a failed
upload never being marked processed, and store calls never originating off the main thread — are
part of stage 9's definition of done.

## Open questions

| # | Question | v1 default |
| --- | --- | --- |
| 1 | Should the processing job emit a machine-readable run manifest (per-event outcomes and URIs) alongside the log? | Not in v1 — the structured log already carries every field, and `ProcessingRun` carries them in-process. Revisit in v0.2; it is roughly twenty lines and turns several CI debugging questions into a `jq` call. |

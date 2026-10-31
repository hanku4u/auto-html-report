---
status: active
description: The two scheduled jobs — the Discoverer port, run_discovery, run_processing, throughput policy, exit codes and worked job scripts.
replaces: docs/SPEC.md §4.8, §4.9, §7.4, §10
---

# 05 — Discovery and processing jobs

Two independently scheduled jobs share only the event store
([04](04-events-and-stores.md)). Either can fail, be re-run, be throttled, or run on a different
cadence without affecting the other. See ADR-0008.

## 1. Discovery

```python
# auto_html_report/application/discovery.py
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
**once**, in `application/execution.py`, parameterised by the work being run and by a vocabulary that
names its log events (`block.*` or `discovery.*`) — see [ADR-0029](10-decision-log.md#adr-0029--timeout-retry-and-hang-guard-mechanics-live-in-one-executor).
`collect_blocks` and `run_discovery` supply their own attempt callable and map the shared result to
their own outcome type. Discoverers and blocks therefore cannot drift apart in behaviour, which is the
point.

A discoverer is identified by its **class name**: `DiscoveryOutcome.discoverer` and every
`discovery.*` log line carry it, so a run rejects two instances of the same class rather than labelling
them identically. A second source needs its own `Discoverer` subclass.

**Exception text is not redacted.** A discoverer's exception message is recorded in its
`DiscoveryOutcome` and logged; the framework redacts `Settings`, not a string you build. Do not put a
token, password or full request URL in a discoverer's exception — log the identifier, not the credential.

`discover()` is materialised with a ceiling of **10,000 events per run**
(`MAX_EVENTS_PER_DISCOVERER`); exceeding it fails that discoverer. The framework bounds *time* — budgets,
retries, the hang guard — but not memory, and the hang guard only stops *waiting* for a wedged worker, it
cannot stop it. Without the ceiling, an endless iterable from a broken or hostile upstream would grow one
worker's buffer for as long as the process lives.

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
    fail_events_with_failed_blocks: bool | None = None,
) -> ProcessingRun:
    """Fetch pending events, build each event's report, write it, and update the store."""
```

A run takes a **bounded batch**: `AUTO_HTML_REPORT_PROCESSING_LIMIT` (default 500) is the ceiling, and
`limit` may lower it but never raise it — `None` means the ceiling. A caller that asks for more is
clamped and logged as `processing.capped`, because silently processing fewer events than requested is
indistinguishable from a shorter queue. Workers are submitted as a sliding window of at most
`AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS`, not one future per event, so a full batch does not become a full
batch of in-flight reports.

Behavioural rules, each of which has a test in [09](09-testing-and-acceptance.md):

1. **The factory supplies everything event-specific.** For each event,
   `get_report_factory(event.event_type)(event, context)` produces the `ReportDefinition`. There is
   no other path from an event to a block set.
2. **A missing factory does not consume the event.** `ConfigurationError` is recorded against that
   event, the event is left `NEW`, and the run continues with other types. A half-deployed job must
   not silently eat events it cannot render.

   **Consequence, stated plainly:** because no attempt is counted, ADR-0019's retirement never applies
   to such an event, and `pending()` is oldest-first — so an event type that is *permanently*
   unregistered (a factory that was removed, or a type nobody intends to handle) is returned at the head
   of every run. With more of them than `AUTO_HTML_REPORT_PROCESSING_LIMIT`, they fill each batch and starve
   the types behind them, while the run exits `2` on every schedule. The remedies are operational:
   register the factory, run the job filtered to a handled type (`run_processing(event_type=...)`), or
   retire the offending events with SQL.
3. **`mark_processed` is called only after the writer returns.** A report that failed to upload must
   not be recorded as delivered, or it is lost with nobody the wiser.
4. **A report whose blocks failed is still a report.** It was written successfully, and the error
   cells *are* the deliverable. Whether that counts as a failed event is decided by
   `AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR` (default `true`): when true the event is marked `FAILED` for
   operator follow-up and the run exits `1`; when false it is `PROCESSED` and the run exits `0`.
   `fail_events_with_failed_blocks` overrides the setting for one run, and `None` — the default —
   means "use the setting", which is what a job script that does not mention it expects.
5. **`run_processing` never raises for a data-level or operational failure.** A missing factory, a
   failing writer and a store call that fails mid-run are recorded in the affected
   `EventProcessingOutcome` and ranked by `exit_code_for` below. A store or writer failure also
   **stops the run** after one recorded outcome: both are systemic, every later event would fail the
   same way, and continuing a writer outage would burn every event's attempt budget and retire a
   healthy queue. The only exception that escapes is a `pending()` call that cannot read the store,
   because no outcome exists yet to report on; [§6](#6-example-job-scripts) shows how a script maps
   that. See [ADR-0030](10-decision-log.md#adr-0030--the-processing-job-composes-at-the-root-and-records-operational-failures).

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
| `AUTO_HTML_REPORT_MAX_CONCURRENT_REPORTS` | Reports processed simultaneously per run | `1` |
| `AUTO_HTML_REPORT_PROCESSING_LIMIT` | Events one run takes from the queue (a ceiling for `limit`) | `500` |

**The default is 1, deliberately.** With sequential events, every store call happens on the main
thread, which is what makes the single-writer assumption in [04](04-events-and-stores.md) safe and
keeps `mark_processed` trivially correct. Raising the value is supported: report building happens
in worker threads and **all store writes are marshalled back onto the main thread**, so the store is
never touched concurrently regardless of the setting.

Set it higher when a run's event count makes wall-clock time a problem. The per-report block pool
is sized `min(AUTO_HTML_REPORT_MAX_WORKERS, len(blocks))`, so raising report concurrency multiplies the
number of in-flight HTTP calls — size it against the API's rate limits, not against CPU. See
ADR-0020, and note that if a single run ever needs to process hundreds of events, sharding by
event type across several jobs is usually a better answer than one very wide job.

The store is written by one process. Concurrent job *processes* against the same store are not
supported in v1 — `pending()` and `mark_*` are not atomic across processes, so two runs can claim
the same event. Claim/lease semantics are a v0.2 concern, recorded in
[10 — Decision log](10-decision-log.md) as ADR-0007 and ADR-0020.

**One run per process.** The runner stops *waiting* for a worker it has abandoned (a report that overran
its blocks' budgets) but cannot stop the thread, which finishes its own I/O (ADR-0005). The job scripts
therefore run one scheduled invocation per process, and exit when it returns. A long-lived scheduler that
called `run_processing` in a loop would accumulate one orphaned worker per wedged report; use a process per
run (cron, a CI job, a container invocation), as [§6](#6-example-job-scripts) does.

## 5. Exit codes for CI

There is no CLI (ADR-0001), so the framework supplies the vocabulary and the client's job script
decides when to exit.

```python
class ExitCode(IntEnum):
    SUCCESS             = 0   # every event processed, every block OK
    PARTIAL_FAILURE     = 1   # reports produced, but at least one block or event failed
    CONFIGURATION_ERROR = 2   # bad settings, or a missing factory for an event's type
    STORE_ERROR         = 3   # event store unreachable or corrupt
    OUTPUT_ERROR        = 4   # a report could not be written


def exit_code_for(run: DiscoveryRun | ProcessingRun) -> ExitCode:
    """Map a finished run to a process exit code. Never raises."""
```

**Precedence, when several conditions apply.** The ranking reads the `error_type` values the runner
recorded, because operational failures are recorded rather than raised (ADR-0030). The most severe
wins, in this order:

| Rank | Code | Applies when |
| --- | --- | --- |
| 1 | `CONFIGURATION_ERROR` (2) | An outcome recorded a `ConfigurationError`: no factory for that event's type, or a discoverer that broke its contract. |
| 2 | `STORE_ERROR` (3) | An outcome recorded a `StoreError`: a store call failed mid-run. |
| 3 | `OUTPUT_ERROR` (4) | An outcome recorded an `OutputError`: a report was built but could not be written. |
| 4 | `PARTIAL_FAILURE` (1) | Any other event failed to process — including a definition the renderer refused because of the event's own data (`EventDataError`), which is retried and eventually retired rather than blamed on the deployment — or a block failure was turned into a failed event by `AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR`. |
| 5 | `SUCCESS` (0) | None of the above. |

Failures **outside** a run are not ranked by `exit_code_for`, because there is no run to pass it:
unreadable settings, a store that will not open, and a writer that cannot be built all raise during
setup. Discovery adds one more: it persists *after* its concurrent phase, so a `save()` that fails
raises `StoreError` from `run_discovery` with no run to return. A job script that wants `2`, `3` or `4`
for any of those catches them and returns the matching code — [§6](#6-example-job-scripts) shows the
shape.

Consequence: a report containing three failed blocks exits `1`, so the CI job goes amber and
somebody looks. That is intended. Operators who consider a partly-red report acceptable set
`AUTO_HTML_REPORT_FAIL_ON_BLOCK_ERROR=false` and get `0`.

## 6. Example job scripts

```python
# my_jobs/discover_wafers.py
import sys

from auto_html_report import (
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

from auto_html_report import (
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

Both scripts import `auto_html_report.reports.manufacturing` (or whatever modules hold the report
factories) at the top, because a factory registers itself on import. Forgetting that import is the
one mistake these scripts make, and the failure mode is loud rather than silent: every event
records a `ConfigurationError` and exits `2`.

### Setup failures exit before a run exists

`load_settings`, `configure_logging`, `build_output_writer` and opening the store all raise rather than
returning a run, so their exit codes are the script's to choose. The examples above keep `main` small
for readability; a production script wraps the setup:

```python
from auto_html_report import ConfigurationError, ExitCode, OutputError, StoreError


def main() -> int:
    try:
        settings = load_settings()
        configure_logging(settings.log_level, settings.log_format)
        writer = build_output_writer(settings)
        with SqliteEventStore.from_settings(settings) as store:
            run = run_processing(
                store=store,
                context=new_report_context(settings),
                writer=writer,
            )
    except ConfigurationError:
        return ExitCode.CONFIGURATION_ERROR
    except StoreError:
        return ExitCode.STORE_ERROR
    except OutputError:
        return ExitCode.OUTPUT_ERROR
    return exit_code_for(run)
```

The three are siblings, so the order is a reading aid rather than a requirement; what matters is that
they are named individually. A bare `except Exception` would turn an unknown bug into a plausible exit
code.

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

**Status:** accepted and deferred for v1. The default above is what the code implements; revisit
in v0.2 (the conventions require an `active` document to name its default, not to have none).

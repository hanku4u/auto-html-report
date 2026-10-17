---
status: proposed
description: Report data types, the injected contexts, the three public entry points, the block and report registries, and how predefined and dynamic reports are expressed.
replaces: docs/SPEC.md §4.4, §4.5, §4.6, §4.11, §4.12
---

# 03 — Report API and registry

## 1. Report data types

All four types are frozen dataclasses. `ReportDefinition` and `ReportRow` are assembled by callers
and by `collect_blocks`; `Report` and `ReportResult` are assembled by `generate_report`.

```python
@dataclass(frozen=True, slots=True)
class ReportDefinition:
    title: str
    blocks: Sequence[Block[Any]]
    subtitle: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)  # extra header key/value pairs

    def __post_init__(self) -> None:
        """Reject an empty block list and duplicate block names.

        Duplicate names are a programming error, and finding out during a scheduled run
        means a reader cannot tell the two rows apart. Raise ConfigurationError here.
        """
```

```python
class BlockStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True, slots=True)
class BlockOutcome:
    name: str
    status: BlockStatus
    duration_seconds: float
    attempts: int                      # total attempts made, including the first
    error_type: str | None = None      # exception class name; safe for display
    error_message: str | None = None   # truncated; never a traceback (see 06 §3)


@dataclass(frozen=True, slots=True)
class ReportRow:
    item: str                          # Item column text (the block's name)
    html: str                          # inner HTML of the Data cell
    outcome: BlockOutcome
```

> **Note:** there is deliberately no `check` field. The **Check column** is structural — the
> renderer emits an empty `<td class="ai-cell--check">` for every row, and a future version may make
> it interactive. A field that can only ever hold `""` is bloat, so it is not modelled. See
> ADR-0018.

```python
@dataclass(frozen=True, slots=True)
class Report:
    definition: ReportDefinition
    rows: tuple[ReportRow, ...]
    generated_at: datetime             # timezone-aware UTC
    run_id: str

    @property
    def all_succeeded(self) -> bool:
        return all(row.outcome.status is BlockStatus.OK for row in self.rows)

    @property
    def failed_rows(self) -> tuple[ReportRow, ...]:
        return tuple(r for r in self.rows if r.outcome.status is not BlockStatus.OK)


@dataclass(frozen=True, slots=True)
class ReportResult:
    report: Report
    html: str
    location: OutputLocation | None    # None when no writer was supplied
```

**A deliberate non-invariant:** nothing validates that `rows` corresponds one-to-one with
`definition.blocks`. The assembly pipeline is the only producer, so the check would be validating a
state the framework never creates. A hand-built `Report` in a test is free to be inconsistent, and
that is intentional.

## 2. The three public entry points

```python
def collect_blocks(
    blocks: Sequence[Block[Any]],
    *,
    context: ReportContext,
) -> tuple[ReportRow, ...]:
    """Collect and render every block concurrently; return rows in argument order.

    Never raises for a block-level failure: a failed block yields a ReportRow whose
    outcome is FAILED or TIMED_OUT and whose html is the framework's error cell.
    Raises ConfigurationError for an invalid block set (duplicate names, empty list).

    Exposed publicly so a caller can audit outcomes before deciding to write anything.
    """


def render_report(report: Report) -> str:
    """Render a fully-assembled Report to a complete HTML document.

    Pure: no I/O, no clock. ``report.generated_at`` and ``report.run_id`` supply
    everything the document header needs.
    """


def generate_report(
    definition: ReportDefinition,
    *,
    context: ReportContext,
    writer: OutputWriter | None = None,
) -> ReportResult:
    """The public entry point for the common case.

    Composes the two functions above:

        rows   = collect_blocks(definition.blocks, context=context)
        report = Report(definition, rows, generated_at=context.clock.now(),
                        run_id=context.run.run_id)
        html   = render_report(report)
        location = writer.write(filename=report_filename(definition, report),
                                content=html) if writer else None
        return ReportResult(report, html, location)
    """
```

`generate_report` is the entry point for "I have a list of blocks, give me a report". The two
lower-level functions are public so that a caller who wants to inspect outcomes *before* writing,
or to embed the HTML in an email body instead of a file, does not have to reimplement the pipeline.
`generate_report` is a thin composition of them and nothing more — if it ever grows logic of its
own, that logic belongs in one of the two.

## 3. Predefined reports, dynamic lists, and per-event reports

Both requirements are served by one mechanism: `ReportDefinition` is just data, so a "predefined
report" is a function that returns one, and a "dynamic list of blocks" is a `ReportDefinition` the
caller assembled by hand.

```python
# auto_inform/reports/manufacturing.py
from auto_inform import Event, ReportContext, ReportDefinition, register_report

WAFER_FAILURE_EVENT = "WAFER_FAILURE"


@register_report(WAFER_FAILURE_EVENT)
def wafer_failure_report(event: Event, context: ReportContext) -> ReportDefinition:
    """The event supplies the report context: which lot, which tool, which window."""
    lot_id = event.payload["lot_id"]
    return ReportDefinition(
        title=f"Failed wafer — {event.event_id}",
        subtitle=str(event.payload.get("summary", "")),
        metadata={"Lot": lot_id, "Tool": str(event.payload["tool_id"])},
        blocks=[
            WaferMapBlock(lot_id=lot_id),
            MaintenanceHistoryBlock(tool_id=event.payload["tool_id"]),
            TrendChartBlock(metric="yield", tool_id=event.payload["tool_id"]),
        ],
    )
```

```python
# An ad-hoc report, assembled dynamically at runtime
blocks = [block_cls(**params) for block_cls, params in requested_blocks]
definition = ReportDefinition(title="Ad-hoc review", blocks=blocks)
generate_report(definition, context=ctx, writer=writer)
```

The registry keys on **`event_type` only**, and the factory receives the whole `Event`, so two
events of the same type can produce entirely different block sets. This is how "another event needs
a completely different context" is satisfied without a second framework mechanism.

> **Convention:** a `ReportFactory` closes over the event and binds the event's fields into each
> block's **constructor**. The event is *not* stuffed into `ReportContext`. Explicit parameters keep
> blocks honest — a block that declares `tool_id` in its signature cannot silently depend on
> report-wide ambient state that a caller forgot to populate.

## 4. The registry

Blocks are added to this package directly and are never shipped from elsewhere, so the mechanism is
deliberately small — no `entry_points`, no import hooks. The *interface* stays plugin-shaped, so a
future decision to load external packages changes only `registry.py` (ADR-0003).

```python
# auto_inform/registry.py
def register_block(cls: type[Block[Any]]) -> type[Block[Any]]:
    """Class decorator. Uses cls.display_name as the key.

    A duplicate key raises ConfigurationError: it means someone copy-pasted a block and
    forgot to rename it, and knowing at import time beats a mystery row.
    """

def import_builtin_blocks() -> None:
    """pkgutil.iter_modules over auto_inform.blocks, importing every submodule.
    Idempotent; called lazily on the first registry lookup."""

def get_block(key: str) -> type[Block[Any]]: ...
def available_blocks() -> Mapping[str, type[Block[Any]]]: ...

ReportFactory = Callable[[Event, ReportContext], ReportDefinition]

def register_report(event_type: str) -> Callable[[ReportFactory], ReportFactory]:
    """Register the factory that knows how to build the report for an event type."""

def get_report_factory(event_type: str) -> ReportFactory:
    """Raises ConfigurationError when no factory is registered for the type."""
```

Lookups are **lazy**, not import-time, so a caller who only ever passes explicit block instances
never pays for scanning the block package. Registration is idempotent under re-import, which matters
because `pkgutil` scans are easy to trigger twice in a long-lived process.

> **Gotcha:** `import_builtin_blocks()` only walks `auto_inform.blocks`. A `@register_block` class
> defined in a *client's* module registers only if that module is actually imported, so
> `get_block("my_block")` fails depending on import order. Passing instances explicitly always
> works and is the recommended path for client-authored blocks.

## 5. The injected contexts

Four frozen dataclasses carry everything a block or discoverer is allowed to depend on. They are
defined here because they appear in every public signature in §2 and §3; where they are *built* is
[07](07-configuration-and-logging.md#3-context-factories).

```python
@dataclass(frozen=True, slots=True)
class RunMetadata:
    run_id: str              # uuid4 hex, generated once per job run
    started_at: datetime     # timezone-aware UTC


@dataclass(frozen=True, slots=True)
class ReportContext:
    settings: Settings       # immutable view of env-derived config
    http: HttpClient         # injected port; stub in v1 (see 07 §2)
    logger: logging.Logger
    clock: Clock             # injectable for deterministic tests
    run: RunMetadata


@dataclass(frozen=True, slots=True)
class RenderContext:
    """What render() is allowed to touch. Slimmer on purpose: no HTTP, no store, no logger."""

    generated_at: datetime
    report_title: str


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    settings: Settings
    http: HttpClient
    logger: logging.Logger
    clock: Clock
    run: RunMetadata
```

`RenderContext` deliberately carries **no logger**: `render()` is pure
([02 §3](02-block-authoring.md#3-render--the-pure-phase)), and logging is a side effect. A block that
needs to log does so in `collect()`.

> **Gotcha:** `logging.Logger` is thread-safe, and `Settings` and the contexts are frozen, so the
> shared context is safe to hand to every worker in a report. Anyone tempted to add a mutable cache
> to `ReportContext` must make it thread-safe and document that — the v1 answer is "don't". Use
> per-instance state on the block, which is single-threaded per block instance.

The event being processed is **not** carried on `ReportContext`: a `ReportFactory` binds the event's
fields into each block's constructor instead, so a block cannot silently depend on ambient state
(§3).

The four context types are defined in an `auto_inform/application/context.py` module, not in
`domain/`: they bundle injected dependencies, and putting them in the domain would invert the
layering. `domain/block.py` names two of them under `TYPE_CHECKING` only — see
[01 §4](01-overview.md#4-architectural-overview) rule 1.

`Clock` is a three-method port rather than a single `now()`. `now()` returns wall-clock time for
timestamps a human reads; `monotonic()` returns a non-decreasing value for measuring *elapsed* time —
durations, timeouts and retry backoff — because a wall clock stepped by NTP mid-run can yield a
negative duration or a timeout that never fires. `sleep()` is the only way the framework waits, and
putting the wait behind the same seam is what makes the hermetic testing rule in
[09 §2](09-testing-and-acceptance.md#2-hermeticity-rules) achievable rather than aspirational. The
default implementation lands in stage 7. See ADR-0022.

The five factory helpers that build these objects from the environment — `load_settings`,
`configure_logging`, `new_report_context`, `new_discovery_context`, `build_output_writer` — are
specified in [07 §3](07-configuration-and-logging.md#3-context-factories). They are mentioned here
only because the job scripts that use this API depend on them.

## Testing

- Domain objects: plain unit tests. Duplicate block names rejected; empty report rejected; frozen
  semantics hold; `all_succeeded` and `failed_rows` agree with the row statuses.
- `collect_blocks`: tested in [02](02-block-authoring.md#testing) for execution semantics, and here
  for its ordered `tuple` return and its error-cell substitution.
- Registry: `register_block` twice with the same `display_name` raises; `import_builtin_blocks` is
  idempotent; `get_report_factory` raises `ConfigurationError` for an unregistered type.
- `render_report` is covered by the golden-file tests in [09](09-testing-and-acceptance.md).

## Dev plan

Implemented across stages 2, 4 and 6 of [11-implementation-plan.md](11-implementation-plan.md): the
report data types and contexts of §1 and §5 in stage 2, the registry in stage 4, and the three entry
points of §2 in stage 6 — which is the point at which the library becomes usable without the job
layer.

## Open questions

| # | Question | v1 default |
| --- | --- | --- |
| 1 | Does an ad-hoc report need an identity in the event store, or is it always written and forgotten? | Always written and forgotten. It has no `Event`, so it has no lifecycle. If ad-hoc reports later need tracking, that is a new port, not a change here. |

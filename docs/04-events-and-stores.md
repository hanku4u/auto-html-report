---
status: proposed
description: The Event value object, the EventStore port, the shared schema, lifecycle and retirement rules, and the SQLite and PostgreSQL implementations.
replaces: docs/SPEC.md §4.7
---

# 04 — Events and event stores

## 1. The Event value object

```python
# auto_inform/domain/event.py
@dataclass(frozen=True, slots=True)
class Event:
    event_type: str                 # e.g. "WAFER_FAILURE"
    event_id: str                   # stable and unique within event_type
    payload: Mapping[str, Any]      # JSON-serialisable, event-type-specific
    discovered_at: datetime         # timezone-aware UTC
    source: str                     # which discoverer/API produced it
```

`Event` is a **pure value object with no lifecycle state**. Lifecycle lives in the store and is
updated through explicit calls, because a frozen record that mutates its own status is a lie, and
because two independently scheduled jobs share the record — one of them only ever reads.

The framework never validates `payload` against any schema. Shape is the `ReportFactory`'s
responsibility, because only the factory knows what a given event type means. See ADR-0006.

## 2. The EventStore port

```python
# Shown together for readability, but these two live in different layers:
#   EventStatus -> auto_inform/domain/event.py   (a pure domain state)
#   SaveResult and EventStore -> auto_inform/application/ports.py   (a port and its result type)
class EventStatus(StrEnum):
    NEW = "NEW"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SaveResult:
    new: int                # rows inserted
    duplicates: int         # rows already present, left untouched


class EventStore(Protocol):
    def save(self, events: Iterable[Event]) -> SaveResult: ...

    def pending(
        self,
        *,
        event_type: str | None = None,
        limit: int | None = None,
    ) -> list[Event]: ...

    def mark_processed(self, event: Event, *, output_uri: str) -> None: ...

    def mark_failed(self, event: Event, *, reason: str) -> None: ...

    def close(self) -> None: ...

    def __enter__(self) -> "EventStore": ...
    def __exit__(self, *exc_info: object) -> None: ...
```

### 2.1 `save()` — insert-only

Insert-only, on the conflict key `(event_type, event_id)`. **An existing row is left completely
untouched**, including its status, attempts and payload.

> **Consequence, stated plainly:** if discovery first persists a malformed payload, re-discovery
> will not repair it. The row must be deleted by an operator. This is the deliberate trade: the
> alternative — letting a re-discovery overwrite a `PROCESSED` row's payload — risks rewriting the
> inputs of a report that has already been delivered, which is worse than an operator's `DELETE`.
> The conflict key is stable by construction, since `event_id` is required to be stable within an
> `event_type`.

`save()` is idempotent: calling it twice with the same events reports the second batch entirely as
duplicates.

A duplicate whose stored `source` differs is **not** counted as a duplicate: `save()` raises
`StoreError` and rolls the batch back. Two discoverers may legitimately share an `event_type` — that is
how two feeds cover one kind of event — and `event_id` is required to be unique within it, so a
cross-source collision means two different events are claiming one identity. Insert-only storage would
let the first silently suppress the second for ever, with an inflated duplicate count as the only trace.
One lookup per duplicate turns that silent loss into a loud failure.

### 2.2 `pending()` — the work queue

Returns events with `status = NEW` and `attempts < max_attempts`, **oldest first** by
`discovered_at`, optionally filtered by `event_type` and capped by `limit`. Never returns
`PROCESSED` or retired `FAILED` rows.

`limit=None` reads the whole queue and is a **diagnostic** mode, not the processing path: the processing
job always passes a limit, so a large backlog is never pulled into memory in one go. Stage 9 owns that
policy; the port keeps the unbounded form because an operator inspecting a store needs it.

### 2.3 `mark_processed()` and `mark_failed()` — lifecycle

| Call | Effect |
| --- | --- |
| `mark_processed(event, output_uri=…)` | `status = PROCESSED`, `processed_at = now`, `output_uri` recorded, `last_error` cleared. |
| `mark_failed(event, reason=…)` | `attempts += 1`. If `attempts < max_attempts`, the row stays `NEW` so the next run retries it. If it reaches `max_attempts`, `status = FAILED` and the row is **retired** — it is never returned by `pending()` again. `last_error` always records the reason. Returns a `FailureRecord(attempts, retired)`: the runner logs `event.failed` and `event.retired` from the store's own count rather than re-deriving the boundary in a second place. |

Both transitions apply only to a row whose status is `NEW`. A call for an event that is already
`PROCESSED` or retired raises `StoreError` rather than silently doing nothing, because a second
transition is a bug in the caller and a silent no-op would hide it: the event would look delivered in
the log while its row said otherwise. The store has no read-by-id method in v1, so diagnosing a retired
event means a SQL query — deliberate, since retention policy belongs to whoever owns the data.

**Retirement is what stops a permanently broken event from starving the queue.** `pending()` is
oldest-first, so before retirement existed, one permanently failing event would be returned at the
head of every single run, consuming the budget of everything behind it, forever. The `attempts`
column and `AUTO_INFORM_EVENT_MAX_ATTEMPTS` (default `3`) exist to give a failing event a bounded
number of tries and then take it out of the way. See ADR-0019.

`mark_processed` is called **only after** the output writer has returned successfully — see
[05](05-discovery-and-processing.md#3-processing-order-per-event).

### 2.4 Thread affinity

**All `EventStore` methods must be called from the thread that constructed the store.** The
processing job marshals every store call onto its main thread;
[05 §4](05-discovery-and-processing.md#4-throughput-policy) explains why this is a requirement
rather than an accident. This matters most for SQLite, whose connections are thread-affine by
default, but the port forbids assuming otherwise for any implementation.

### 2.5 Lifecycle

Stores are context managers. `close()` is idempotent and must be safe to call after an exception.
Because the store owns a file handle (SQLite) or a connection pool (PostgreSQL), a scheduled job
must close it even when a report fails — the job scripts in
[05](05-discovery-and-processing.md#6-example-job-scripts) use `with`.

## 3. Schema

Identical semantics in both backends. SQLite stores `payload` as `TEXT` holding JSON; PostgreSQL
uses `JSONB` and `TIMESTAMPTZ`.

```sql
CREATE TABLE IF NOT EXISTS events (
    event_type    TEXT        NOT NULL,
    event_id      TEXT        NOT NULL,
    payload       TEXT        NOT NULL,   -- JSON
    discovered_at TIMESTAMPTZ NOT NULL,
    source        TEXT        NOT NULL,
    status        TEXT        NOT NULL,   -- NEW | PROCESSED | FAILED
    attempts      INTEGER     NOT NULL DEFAULT 0,
    last_error    TEXT,
    output_uri    TEXT,
    processed_at  TIMESTAMPTZ,
    PRIMARY KEY (event_type, event_id)
);
CREATE INDEX IF NOT EXISTS events_pending_idx
    ON events (status, event_type, discovered_at);
```

DDL is applied idempotently on first open, so a fresh SQLite path needs no setup step. There is no
migration framework in v1: the schema is created if absent and never altered. A schema change is a
deliberate, reviewed operation, not something that happens implicitly on open.

## 4. Implementations

| Class | Backend | Notes |
| --- | --- | --- |
| `SqliteEventStore(path)` | stdlib `sqlite3` | **Default.** WAL journal mode is enabled for the whole connection lifetime: it lets a reader proceed while the store holds a write, which matters because the discovery and processing jobs may overlap. `AUTO_INFORM_SQLITE_PATH` defaults to `<output_dir>/events.sqlite3`. A new file is created **owner-only (0600)** and a new directory 0700, matching the report writer's policy ([06 §4](06-output-html.md#4-the-trust-boundary)); SQLite's own default is 0644 modified by the umask, and the `-wal`/`-shm` siblings inherit the database's mode. An existing file keeps the mode its operator chose. File modes are POSIX semantics, as for the local report writer ([06 §1](06-output-html.md#1-writers)): `O_NOFOLLOW` is applied where the platform provides it, and Windows is not a supported target for this store either. |
| `PostgresEventStore(dsn, *, max_attempts)` | `psycopg[binary]` — extra `postgres` | Client supplies the DSN via environment. `save()` uses `ON CONFLICT (event_type, event_id) DO NOTHING`, matching the insert-only rule. `mark_failed` uses a single `UPDATE … SET attempts = attempts + 1, status = CASE WHEN attempts + 1 >= %s THEN 'FAILED' ELSE 'NEW' END` so that the increment and the retirement test cannot race within a statement. |

Both are constructed explicitly; there is no hidden singleton:

```python
store = SqliteEventStore.from_settings(settings)
store = PostgresEventStore.from_settings(settings)      # reads AUTO_INFORM_POSTGRES_DSN
```

## 5. Sizing and retention

A row costs roughly a few hundred bytes plus the payload. At a rate of 200 events per day, five
years of history is under 100 MB, which SQLite handles comfortably. **Nothing prunes the table in
v1.** A `prune(before: datetime) -> int` method is the natural v0.2 addition, and is deliberately not
in this document because retention policy is a decision for whoever owns the data, not for the
framework.

The default SQLite path sits beside the report output directory so that the event history travels
with the artefacts it describes.

## Testing

- One shared behaviour suite, run against **both** implementations, covering: `save` is idempotent
  and reports duplicates; re-discovery does not reset `PROCESSED`; `save` does not overwrite a
  `PROCESSED` row's payload; `pending` excludes `PROCESSED` and retired rows and is oldest-first;
  `pending(limit=…)` is honoured; `mark_failed` increments and retires exactly at `max_attempts`;
  `mark_processed` clears `last_error`; `close()` is idempotent.
- SQLite tests run against a temporary file via `tmp_path`. PostgreSQL tests are marked
  `@pytest.mark.postgres` and skipped unless `AUTO_INFORM_TEST_POSTGRES_DSN` is set, so CI without a
  Postgres service stays green. See [09](09-testing-and-acceptance.md).
- Two of the cases above are asserted in each **backend's own** module, against its raw storage: that a
  saved payload is never overwritten, and that `last_error` is cleared by a later success. The port
  deliberately exposes no read-by-id method, so those properties are not observable through it. Stage 11
  owes the PostgreSQL equivalents rather than a claim that the shared suite covers them.

## Open questions

| # | Question | v1 default |
| --- | --- | --- |
| 1 | Is a single shared events table sufficient for all report families, or do some families need isolation (separate store/database, separate retention)? | Single shared table. Isolation can be achieved today by pointing a job at a different store entirely, since the store is injected. |

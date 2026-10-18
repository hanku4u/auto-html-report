# FIXME — stage-9-processing review findings

Findings from a review of `stage-9-processing` (head `94d0ca3`), the commit
`feat: add the processing job and exit codes (stage 9)`.

**Comparison base.** The local `main` is stale (`b16b549 first commit`). The real base is `origin/main`
(`09b6f7d`, the stage-8 merge). `stage-9-processing` is one commit ahead of it: 26 files, +1992/−143.
That commit is what this document reviews.

At review time the branch is green: `uv run pytest` (**540 tests**), `uv run ruff check`,
`uv run ruff format --check` and `uv run mypy` all pass. Every item below is something those checks do
not catch.

## Severity legend

| Severity | Meaning |
| --- | --- |
| **P1** | Must fix before merge: a documented guarantee is false, with wrong control flow or exit code. |
| **P2** | Should fix: wrong data or a real availability risk on a path the tests do not exercise. |
| **P3** | Hardening: a contract mismatch or a cheap fix for a known risk. |
| **P4** | Minor / test gap / documentation accuracy: worth a line, not worth blocking. |

---

## Summary

| # | Item | Severity | Status |
| --- | --- | --- | --- |
| 1 | `_classify_failure`'s fallback lets an unrelated class named `OutputError` abort the run and exit 4 | **P1** | [ ] |
| 2 | `run_processing` trusts `settings.processing_limit`; a zero silently processes nothing | **P3** | [ ] |
| 3 | `docs/05` and `exit_codes.py` say a refused definition ranks as a configuration error; it ranks partial | **P3** | [ ] |
| 4 | No per-report deadline covers a hung writer | **P3** | [ ] |
| 5 | `Settings.processing_limit` was inserted mid-dataclass, shifting the positional order | **P4** | [ ] |
| 6 | Missing-factory events are never retired, so a permanently unregistered type can fill the batch | **P4** | [ ] |

---

## 1. `_classify_failure`'s fallback lets an unrelated class named `OutputError` abort the run and exit 4 — **P1**

**Where:** `src/auto_inform/processing.py:267-282` (`_classify_failure`), used by `_commit`

**What's wrong.** The module docstring, ADR-0030 and the `docs/11` "Review" paragraph all state that
failure classification is by **type**, not by name: a writer subclass is filed with `OutputError`, and
*"an unrelated class that merely shares the name `OutputError` must do neither"* (must not stop the run,
must not exit 4). The `isinstance` checks do that for the three framework types — but the fallback for
everything else returns `type(exc).__name__`, which can be exactly `"OutputError"`, `"StoreError"` or
`"ConfigurationError"` for a class from a different library.

**Evidence.** A writer raising a locally defined `class OutputError(Exception)` (same `__name__`, not the
framework's):

```
writer raising an unrelated OutputError ->
  outcomes: [('a', 'OutputError')]
  exit code: 4
  pending: ['a', 'b']
```

The run stopped after one event (`b` was never processed) and `exit_code_for` returned 4. Both are
exactly what the commit says an unrelated class must not do. The same fallback makes a same-named
`StoreError` abort with exit 3 and a same-named `ConfigurationError` exit 2.

The existing test `test_a_writer_subclass_should_be_classified_by_type_not_by_name` covers a *subclass*
of the framework type, which `isinstance` catches; it does not cover the unrelated-class case.

**Impact.** A third-party library with an `OutputError` (a storage client is a plausible source) can
abort a processing run after one event and send CI to the wrong exit code. The behaviour contradicts the
ratified decision and the documentation that describes it.

**Fix.** Make the unknown case non-colliding. For example, add a distinct constant:

```python
UNCLASSIFIED_FAILURE = "UnclassifiedError"
...
return UNCLASSIFIED_FAILURE
```

and carry the real type in the message/log (`_record_failure` already logs a reason; add the exception
type to `event.failed`'s `extra`, or prefix the reason with `type(exc).__name__`). `_ABORTING_FAILURES`
then cannot match an unrelated name, and `exit_code_for` falls through to `PARTIAL_FAILURE`.

**Test to add.** A writer raising a `type("OutputError", (Exception,), {})` instance must be a partial
failure: the run continues, the event is counted, and the exit code is 1. The same control for
`StoreError` and `ConfigurationError`.

**Related, smaller:** `exit_code_for` ranks exact strings, and discovery outcomes carry
`type(exc).__name__` from `execute_tasks`, so a subclass of `StoreError`/`OutputError`/`ConfigurationError`
raised by a discoverer also ranks as a partial rather than its base. Either normalise discovery's outcome
classification by `isinstance` (as the processing runner now does) or document that the ranking is exact
by name.

**References:** `docs/10-decision-log.md` ADR-0030; `docs/11` stage 9 "Review"; `docs/05` §5.

---

## 2. `run_processing` trusts `settings.processing_limit`; a zero silently processes nothing — **P3**

**Where:** `src/auto_inform/processing.py:159-171`

**What's wrong.** The explicit `limit` argument is validated (`< 1` raises `ValueError`), but
`context.settings.processing_limit` is not. `load_settings` bounds it, yet the project repeatedly notes
that `Settings` can be constructed directly — `execution._validate` defends itself for exactly that
reason. A direct `Settings(processing_limit=0)` makes `ceiling = 0`, `requested = 0`,
`pending(limit=0)` returns nothing, and the run returns an empty `ProcessingRun` that `exit_code_for`
calls a success.

**Evidence.**

```
processing_limit=0 -> outcomes: 0 | pending: ['x']
```

A negative value is worse: it reaches `pending(limit=-5)` and raises a raw `ValueError` from the store
rather than a configuration error naming the setting.

**Impact.** A misconfigured job looks permanently healthy and does no work — the exact failure the
`event_type` filter validation exists to prevent ("a typo'd event type reads an empty queue and exits
SUCCESS for ever").

**Fix.** Validate the setting in `run_processing` before reading the queue:

```python
ceiling = context.settings.processing_limit
if ceiling < 1:
    raise ConfigurationError(f"processing_limit is {ceiling!r}; it must be at least 1")
```

(or clamp with a `processing.capped`-style warning, but raising is clearer for a setting).
Add tests for `0` and a negative value on a directly constructed `Settings`.

**References:** `docs/07` §1.1; `docs/05` §4; `src/auto_inform/application/execution.py` (the same
defensive pattern for `block_retries`).

---

## 3. `docs/05` and `exit_codes.py` say a refused definition ranks as a configuration error; it ranks partial — **P3**

**Where:** `docs/05-discovery-and-processing.md` §5 precedence table; `src/auto_inform/exit_codes.py:10-11`

**What's wrong.** Two places say a definition refused by the renderer is a `ConfigurationError` and ranks
2. The code does the opposite, deliberately: `_classify_failure` maps a framework `ConfigurationError`
raised by the factory/`generate_report` to `EventDataError`, which is not in `_RANKING`, so
`exit_code_for` returns `PARTIAL_FAILURE` (1). ADR-0030 and
`test_a_refused_definition_should_count_an_attempt_and_retire` agree with the code.

- `docs/05` rank 1: *"An outcome recorded a `ConfigurationError`: no factory for that event's type, **a
  definition the renderer refused**, or a discoverer that broke its contract."*
- `exit_codes.py` docstring: *"any `ConfigurationError` -- a factory missing **or a definition refused**,
  which means the deployment is wrong rather than the data"*

**Impact.** A reader implementing CI against the table expects exit 2 for a payload-refused definition;
the runner gives 1. The distinction is deliberate (the data is the event's problem, not the deployment's),
so the documents should say so.

**Fix.** Move "a definition the renderer refused" from the rank-1 row to the rank-4 (partial) row in
`docs/05`, and correct the `exit_codes.py` docstring. Keep "a discoverer that broke its contract" under
rank 1, since discovery records the exact `ConfigurationError` name.

**References:** `docs/10` ADR-0030; `tests/unit/test_processing.py`
(`test_a_refused_definition_should_count_an_attempt_and_retire`).

---

## 4. No per-report deadline covers a hung writer — **P3**

**Where:** `src/auto_inform/processing.py:183-184` (`window.popleft().result()`)

**What's wrong.** `_commit` and the pool have no timeout on `future.result()`. The module docstring and
`docs/11` justify this for **factories** ("pure and fast; a hung factory is a defect like a hung
`__init__`"), but a worker also calls `writer.write`, and an S3 upload (stage 11) can hang on the
network for reasons that are not a user-code defect. Blocks have a budget and a real-time hang guard
(ADR-0027); the report-level writer has neither, and `future.result()` without a timeout waits for ever.

**Impact.** A stalled S3 client wedges the whole processing job — no timeout, no outcome, no exit code.
The default local writer is fast, so this is latent until stage 11, but the port now explicitly allows
concurrent writers and the stage-9 runner is where the deadline would live.

**Fix.** Decide and record: either the S3 writer must set a client-side timeout (document it in
`docs/06` §1 and `docs/11` stage 11), or add a configurable per-report deadline that covers the writer
(e.g., the sum of the block guards plus a writer allowance) and pass it to `future.result()`. The
"no factory deadline" decision can stand; the writer is a different kind of call.

**References:** `docs/11` stage 9 "The report-level failure decision"; ADR-0027; `docs/06` §1.

---

## 5. `Settings.processing_limit` was inserted mid-dataclass, shifting the positional order — **P4**

**Where:** `src/auto_inform/infrastructure/settings.py:63-70`

`processing_limit` was added **before** `block_timeout_seconds`, so every positional field after it
shifts by one. `Settings` is a public frozen dataclass, and this is the same class of break the
`ReportDefinition.event_id` finding identified in stage 6. All current code uses keywords, but a
consumer constructing `Settings(...)` positionally would silently bind the wrong values.

**Fix.** Append `processing_limit` after the existing fields, or make it keyword-only
(`field(default=500, kw_only=True)`), as `ReportDefinition.event_id` now is.

---

## 6. Missing-factory events are never retired, so a permanently unregistered type can fill the batch — **P4**

**Where:** `src/auto_inform/processing.py:296-314` (`event.skipped`)

A missing factory leaves the event `NEW` and counts no attempt — correct per acceptance criterion 15 and
deliberate, so a half-deployed job picks it up after the fix. But the event is also never aged out, so a
*permanently* unregistered event type (a factory that was removed, or an event type nobody intends to
handle) sits at the head of an oldest-first queue and is fetched again on every run. With more such
events than `AUTO_INFORM_PROCESSING_LIMIT`, they fill the batch and starve the other types behind them,
even though the run exits 2 loudly.

**Fix.** No code change is strictly required: the spec mandates leaving the event `NEW`, and exit 2 is
the signal. Document the consequence and the operational remedy in `docs/05` §2 rule 2 — filter the run
by `event_type` (`run_processing(event_type=...)`) or retire the events with SQL until the deployment is
complete — so an operator with a poisoned queue knows what to do.

---

## Accepted as-is (do not "fix" these)

- **`processing.py` and `exit_codes.py` at the package root.** Same reasoning as ADR-0028: composing a
  report means calling the renderer, and `application/` may not. The runner imports no adapter.
- **Operational failures are recorded, not raised** (ADR-0030). `exit_code_for` can only rank what it is
  given; a `pending()` failure escapes because no outcome exists yet, and `docs/05` §6 shows the script
  wrapper.
- **A store or writer failure stops the run after one recorded outcome.** Continuing through an outage
  would burn every event's attempt budget and retire a healthy queue.
- **`fail_events_with_failed_blocks` defaults to `None`** ("use `AUTO_INFORM_FAIL_ON_BLOCK_ERROR`"); the
  old `False` default would have silently ignored the setting.
- **No report-level deadline for factories.** A factory that blocks is a defect; inventing a timeout
  would abandon legitimate large reports. Item 4 is about the writer, not the factory.
- **The batch is bounded and consumed as a sliding window**, not one future per event. Good for memory
  and for the rate at which a backlog is pulled.
- **`mark_failed` returns `FailureRecord`.** The retirement boundary is the store's decision, and
  re-deriving it in the runner would be a second copy of ADR-0019.

## What is already good (do not regress)

- Store calls happen only in `_commit`, on the calling thread, and the concurrency test (criterion 19)
  proves it at more than one report worker.
- `FakeEventStore` runs the shared store suite, so the double is held to the port rather than to the
  runner's assumptions.
- The sliding window preserves event order through `popleft().result()`, and the abort path cancels the
  queued futures and stops waiting — the same deliberate no-`with` rule as ADR-0005.
- `generate_report` now emits `report.failed` for every post-start failure and `report.finish` only when
  a document exists, so no start is orphaned and no finish claims a file that was never produced.
- `event.skipped` is distinct from `event.failed`, because a missing factory counted no attempt.
- The console formatter indents and escapes the whole traceback block, closing the stage-8 residual.
- `test_doc_imports` has a working control for its empty `PENDING` allowlist, so the mechanism cannot rot
  silently.
- Exit codes are an `IntEnum` with asserted numbers and a single ranked table, which is what a CI script
  can branch on.

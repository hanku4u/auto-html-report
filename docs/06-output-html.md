---
status: proposed
description: Output writers and file naming, the HTML document contract, the escaping trust boundary, and figure embedding.
replaces: docs/SPEC.md §4.10, §6
---

# 06 — Output: writers and HTML conventions

This document is a contract. Clients screenshot these reports, paste them into incident tickets, and
forward the raw file to people with locked-down browsers — so "self-contained" is load-bearing, and
so is escaping.

## 1. Writers

```python
# Defined in auto_inform/domain/report.py, despite being produced only by writers: a pure value
# type belongs in the domain, and ReportResult references it, so putting it in the application layer
# would invert the dependency.
@dataclass(frozen=True, slots=True)
class OutputLocation:
    kind: str          # "file" | "s3"
    uri: str           # "file:///…" or "s3://bucket/key"


class OutputWriter(Protocol):
    def write(self, *, filename: str, content: str) -> OutputLocation: ...


class LocalDirectoryWriter:
    """Default. Creates the directory if missing; writes atomically via temp file + rename."""

    def __init__(self, directory: Path | str) -> None: ...


class S3Writer:
    """Optional (extra: s3). boto3 is imported lazily, so non-S3 users do not pay for it."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        client: Any | None = None,                    # injection point for tests
        server_side_encryption: str | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None: ...

    def write(self, *, filename: str, content: str) -> OutputLocation: ...
```

- `write()` takes a `filename` and content as `str`; writers encode UTF-8. **Writers never invent a
  filename** — see §2.
- Both writers are idempotent for a given filename (overwrite). Filenames are unique per run, so
  re-running a job never destroys the previous report.
- A write failure raises `OutputError` with the underlying cause chained. The caller
  ([05](05-discovery-and-processing.md#2-processing)) must not mark the event processed.
- `build_output_writer(settings)` selects the implementation from configuration — see
  [07](07-configuration-and-logging.md#3-context-factories).
- AWS credentials are not the framework's business: `S3Writer` uses boto3's standard credential
  chain (environment, shared config, instance profile, CI OIDC role). No AWS key is ever read by
  this package.

## 2. Filename and destination

```
<slug(title)>__<slug(event_id)>__<UTC timestamp ISO-8601 basic>.html
# e.g. line-4__daily-health__20261017T090000Z.html
```

```python
def report_filename(definition: ReportDefinition, report: Report) -> str: ...
```

**Ownership rule:** the filename is computed in exactly one place, `generate_report`, by calling
this helper. `OutputWriter` implementations receive a complete filename and never derive one, so
that a local writer and an S3 writer can never disagree about naming. `run_processing` obtains the
URI from `ReportResult.location`, which the writer returned.

- `slug()` lowercases, replaces runs of non-alphanumerics with `-`, strips leading and trailing
  hyphens, and caps length at 60 characters so that combined filenames stay under filesystem and S3
  key limits.
- The timestamp is UTC, formatted `%Y%m%dT%H%M%SZ`, and makes every run's output addressable rather
  than overwriting.
- Default destination: `AUTO_INFORM_OUTPUT_DIR`, default `./reports/`, relative to the process
  working directory.
- S3 keys are `f"{prefix.strip('/')}/{filename}"` when a prefix is configured.

## 3. Document skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LINE 4 — Daily Health — 20261017T090000Z</title>
  <style>/* inlined; no external CSS */</style>
</head>
<body class="ai-report">
  <header class="ai-header">
    <h1 class="ai-header__title">LINE 4 — Daily Health</h1>
    <p class="ai-header__subtitle">Failed wafer — EVT-88213</p>
    <dl class="ai-header__meta">
      <dt>Run</dt><dd>9f2c1ab34de5…</dd>
      <dt>Generated</dt><dd>2026-10-17T09:00:00Z</dd>
      <dt>Lot</dt><dd>LOT-88213</dd>
    </dl>
  </header>

  <table class="ai-table">
    <colgroup>
      <col class="ai-col--item"><col class="ai-col--data"><col class="ai-col--check">
    </colgroup>
    <thead>
      <tr><th scope="col">Item</th><th scope="col">Data</th><th scope="col">Check</th></tr>
    </thead>
    <tbody>
      <!-- one <tr> per ReportRow, in definition order -->
    </tbody>
  </table>

  <footer class="ai-footer">auto_inform 0.1.0</footer>
</body>
</html>
```

The header's `Run` and `Generated` values come from `Report.run_id` and `Report.generated_at`;
`metadata` entries are appended in insertion order after them. The document is rendered by
`render_report(report)`, a pure function with no clock access of its own.

## 4. The trust boundary

The page shell is rendered with Jinja2 with `autoescape=True`. **Every framework interpolation is
escaped** — title, subtitle, metadata values, block names, error text.

`ReportRow.html` is inserted inside the single `|safe` filter in the codebase, in
`rendering/templates/report.html.j2`. That is the trust boundary: **`render()` returns trusted HTML
and is responsible for escaping anything it interpolates from outside the process.**

To make escaping the path of least resistance rather than a lecture, the framework ships
escaping-first helpers:

```python
from auto_inform import html_table, escape, figure_to_data_uri

html_table(headers=["Date", "Engineer"], rows=[(a, b)])   # escapes every cell
escape(user_supplied_string)                              # explicit escape
figure_to_data_uri(fig)                                   # no user data involved
```

A block that hand-writes f-strings around API payloads is an XSS bug waiting for a hostile string in
a vendor name. The blast radius is small while the report is a local file — but these files are also
pushed to S3 and forwarded by email, so payload text is treated as untrusted input. See ADR-0012.

### 4.1 What "self-contained" means precisely

The document must not **load** any external resource: no `<script src>`, no `<link rel=stylesheet>`,
no `@import`, no `<img src>` or `srcset` outside `data:` URIs, no `<iframe>`, no web fonts. It may
contain **anchors** to external URLs — a cross-reference to a ticket system is a legitimate cell
content and does not cause the document to fetch anything.

This distinction matters because the acceptance criteria in
[09](09-testing-and-acceptance.md#3-acceptance-criteria) test for it, and an over-broad rule would
fail a perfectly good report that happens to link to a defect tracker.

## 5. Figures

```python
def figure_to_data_uri(figure: matplotlib.figure.Figure, *, dpi: int = 100) -> str:
    """PNG bytes → 'data:image/png;base64,…' for direct <img src>. Thread-safe."""
```

Rules for the implementation:

- `matplotlib.use("Agg")` is called **before** `matplotlib.pyplot` is imported, inside
  `rendering/figures.py`. There is no display in CI, and importing pyplot first is the classic
  headless-CI trap.
- matplotlib is **imported lazily** — only when a block actually calls `figure_to_data_uri()`.
  Reports built only from HTML tables therefore start fast and stay light.
- matplotlib is **not thread-safe**. A module-level `threading.Lock` in `figures.py` serialises
  figure creation and saving. Charts are usually the slowest part of a report, so this costs less
  than it sounds; the lock is held for rendering only and never across a network call.
- `plt.close(fig)` runs in a `finally` block. Without it, long-running processes accumulate figures
  until matplotlib's figure-count warning floods the log.
- Because figures are created in `render()`, which is pure by contract, figure creation must not
  depend on anything outside the data passed in.

```python
# auto_inform/blocks/wafer_map.py
import matplotlib.pyplot as plt
import numpy as np

from auto_inform import Block, ReportContext, RenderContext, figure_to_data_uri


class WaferMapBlock(Block[np.ndarray]):
    display_name = "Wafer map"

    def __init__(self, *, lot_id: str, name: str | None = None) -> None:
        super().__init__(name=name or f"Wafer map — {lot_id}")
        self._lot_id = lot_id

    def collect(self, context: ReportContext) -> np.ndarray:
        return np.random.default_rng(0).random((25, 25))  # noqa: NPY002

    def render(self, data: np.ndarray, context: RenderContext) -> str:
        fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
        try:
            ax.imshow(data, cmap="RdYlGn", interpolation="nearest")
            ax.set_title(f"{self._lot_id} bin map")
            fig.tight_layout()
            uri = figure_to_data_uri(fig)
            return f'<img class="ai-figure" src="{uri}" alt="Wafer bin map for {self._lot_id}">'
        finally:
            plt.close(fig)
```

> Note the `alt` attribute is built from `self._lot_id`, a constructor parameter. Had it come from
> an API payload, it would need `escape()` first — a hand-written f-string inside an attribute is
> exactly where people forget.

## 6. Row, check and error markup

```html
<tr class="ai-row" data-block="maintenance-history">
  <td class="ai-cell ai-cell--item">Maintenance history — T-104</td>
  <td class="ai-cell ai-cell--data">
    <!-- block.render() output, inserted verbatim -->
  </td>
  <td class="ai-cell ai-cell--check"></td>
</tr>
```

The **Check** cell is emitted empty but structurally present, so a future version can add an
interactive checkbox or a status glyph without changing the document structure (ADR-0018). It is
rendered by the framework and never by a block.

```html
<td class="ai-cell ai-cell--data ai-cell--error">
  <div class="ai-error" role="alert">
    <span class="ai-error__icon" aria-hidden="true">⚠</span>
    <span class="ai-error__type">TimeoutError</span>
    <span class="ai-error__message">maintenance service did not respond</span>
    <span class="ai-error__meta">3 attempts · 61.2 s</span>
  </div>
</td>
```

- Error text is derived from the exception type and a truncated message **only**. Tracebacks go to
  the log, never into the document: reports get emailed to people who should not be reading stack
  frames, and stack frames leak internal hostnames and file paths.
- `AUTO_INFORM_ERROR_DETAIL=summary` (default) shows type plus first line. `detailed` adds the
  exception's `__cause__` chain summary. There is no "full traceback in HTML" mode, on purpose.
- Error cells are distinguishable without colour alone (`⚠` glyph plus class), for accessibility and
  for black-and-white printing.

## 7. Styling

- All CSS inlined in a single `<style>` element. No `<link>`, no `@import`, no web fonts.
- All classes prefixed `ai-`, so a report survives being pasted into another page.
- Column widths via `<colgroup>`: Item 18%, Data 74%, Check 8%; `table-layout: fixed`, so a wide
  chart cannot push the Check column off-screen.
- Images are `max-width: 100%; height: auto`. A 4000 px wafer map must not produce a 4000 px page.
- **No JavaScript in v1.** Sorting and filtering are explicitly deferred. Note that inline JS would
  *not* break containment — it would break *email clients and locked-down viewers*, which is the
  real constraint. The document is a report, not a dashboard.
- Minimal `@media print`: no page break inside a row, header repeats per page.

## Testing

- **Golden-file tests** for the full document against a two-block fixture report. Normalise
  insignificant whitespace before comparing. This makes §3, §6 and §7 executable.
- Containment: assert the rendered document contains no external resource *loads*, per §4.1, while
  permitting anchors. One test with an anchor present, one without.
- Filename: `report_filename` tested for the slug rules — a title containing `/`, an emoji, and a
  300-character title all produce a capped, filesystem-safe name.
- Writers: local writer tested against `tmp_path` for atomic rename and directory creation; S3
  writer tested against an injected stub client for key composition, content type and encryption
  header. No live AWS.
- Figures: smoke test only — build a figure, assert the URI starts with `data:image/png;base64,`
  and that `plt.get_fignums()` is empty afterwards. Do not assert on pixels.

## Dev plan

Implemented across stages 3 and 6 of [11-implementation-plan.md](11-implementation-plan.md): the
rendering layer, template, escaping helpers and figure helper in stage 3, then the writers and
filename helper in stage 6. Stage 3 comes before any block is written, because the trust boundary in
§4 is far cheaper to design once than to retrofit.

## Open questions

| # | Question | v1 default |
| --- | --- | --- |
| 1 | Can a report ever be too large for a single file to be sensible (base64 figures are ~33% larger than the raw PNG)? | Assume not in v1. If a real report exceeds a few MB, the answer is fewer or lower-DPI figures, not a new output format. |
| 2 | S3 object lifecycle, signed URLs, or public-read for browser viewing? | None automated. Consumers generate presigned URLs out of band. Bucket policy is the right place for lifecycle and access rules, not this library — and never public-read, since reports contain yield and maintenance data. |

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[PEP 440](https://peps.python.org/pep-0440/).

## [Unreleased]

The package is now only a report builder, with a much smaller API. Code written against 0.1.0 must be rewritten;
0.1.0 stays on PyPI.

### Added

- `build_report(title, blocks, context)`: the context is a plain dict. Blocks name a plain `gather` function, its
  parameter names say which context keys it needs, and identical calls run once however many blocks need them.
- `Report.save()`, and a `charts` extra for matplotlib.
- The usage guide and design notes are installed with the package, in `auto_html_report/docs/`.

### Changed

- Failing fast: a missing context key raises `MissingContextError` before anything is fetched, and a call that fails
  for good raises `FetchError`. A failure in `render` still shows as an error in that row.
- Retries now apply to each gather call, not each block: three by default, waiting 1, 2 and 4 seconds.
- `html_table` takes `headers` and `rows` positionally. matplotlib and numpy are no longer required.

### Removed

- Event discovery and processing, the event stores, the report registry and exit codes. They remain at the `v0.1.0`
  tag, to come back to later.
- `S3Writer`, the `postgres` and `s3` extras, `Settings` and all `AUTO_HTML_REPORT_*` variables, `ReportContext`,
  `collect()`, `generate_report`, `ReportDefinition`, `HttpClient`, `auto_html_report.testing` and the built-in
  semiconductor blocks, which are now in `examples/manufacturing.py`.

## [0.1.0] - 2026-10-31

### Added

- First public release, under the Apache-2.0 license.
- `Block`, `ReportDefinition` and `generate_report`: concurrent collection with per-block timeouts, retries
  and failure isolation, rendered into one self-contained HTML document.
- The two-phase job model: `run_discovery` and `run_processing`, sharing an `EventStore`, with CI exit codes
  from `exit_code_for`.
- SQLite and local-directory backends by default, with PostgreSQL and S3 behind the `postgres` and `s3` extras.
- The `HttpClient` and `Clock` protocols, an adapter recipe, `auto_html_report.testing.StubHttpClient`, and
  a `py.typed` marker.

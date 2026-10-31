# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[PEP 440](https://peps.python.org/pep-0440/).

## [Unreleased]

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

---
status: proposed
description: The move from a git-installed internal tool to the public PyPI package auto-html-report, covering its identity, dependency policy, public-surface changes, and CI/CD release pipeline.
---

# 12 — Publishing to PyPI

This document specifies the target state and the order of work. It is `proposed`: every statement below
describes what the change will do, and the stages in [§7](#7-dev-plan) move each part into the code and
into the document that owns it. The reasoning is in ADR-0032 to ADR-0035 of
[10-decision-log.md](10-decision-log.md).

## 1. Identity

| | Original | Target |
| --- | --- | --- |
| Distribution name | `auto-inform` | `auto-html-report` |
| Import name | `auto_inform` | `auto_html_report` |
| Environment variable prefix | `AUTO_INFORM_` | `AUTO_HTML_REPORT_` |
| Logger name | `auto_inform` | `auto_html_report` |
| Repository | `hanku4u/auto-inform` | `hanku4u/auto-html-report` |
| Published to | nowhere; installed from git | PyPI |
| License | none | Apache-2.0 |

The rename is complete and has no compatibility shim: no tag or release of the old name exists, so there
is no published interface to keep working. The `ai-` CSS class prefix stays: it is an internal namespace for
the document's own styles, and keeping it limits the golden document's diff to the footer and stylesheet
comment.

The built-in blocks and the manufacturing report family stay in the package. ADR-0003 is unaffected:
built-ins are added to this package directly, and a consumer's own blocks are passed to `ReportDefinition`
as instances, which needs no registration.

## 2. Dependency policy

Runtime dependencies and extras move from exact pins to ranges with an upper bound at the next major
version. `uv.lock` continues to pin everything for this repository's own development and CI.

| Package | Original | Target | Where |
| --- | --- | --- | --- |
| `jinja2` | `==3.1.6` | `>=3.1,<4` | required |
| `matplotlib` | `==3.11.2` | `>=3.7,<4` | required |
| `numpy` | `==2.4.6` | `>=1.24,<3` | required |
| `psycopg[binary]` | `==3.3.6` | `>=3.1,<4` | `postgres` extra |
| `boto3` | `==1.43.114` | `>=1.28,<2` | `s3` extra |

Each floor is the oldest release the full suite passes against on Python 3.11, verified when stage 3 of §7
landed. The `lowest-direct` job in [§5](#5-continuous-integration) keeps that true.

numpy and matplotlib stay required. `wafer_map.py` imports numpy at module scope and the registry scan
imports every module under `blocks/`, so a base install without numpy would break block lookup.

Development tools in the `dev` group keep their exact pins: they never reach a consumer, so the argument in
ADR-0033 does not apply to them.

## 3. Public surface

| Change | Why |
| --- | --- |
| Export `HttpClient` and `Clock` from the package root | A consumer has to implement or annotate against them, and today only private paths reach them. |
| Add `auto_html_report.testing`, holding `StubHttpClient` and `StubNotConfiguredError` | A public home for the fixture client, so consumers can test their blocks and `examples/` stops importing `infrastructure`. It sits at the package root and imports no adapter, so the root layering rule still holds. |
| Add a `py.typed` marker | Without it a type checker may ignore the package's annotations. |
| Keep `register_block` private | Consumers pass block instances; nothing outside this package needs it. |

### 3.1 The default HTTP client

The package ships no real client (ADR-0035). `new_report_context()` and `new_discovery_context()` keep
`http` optional, because the quick start and any report whose blocks do no HTTP must keep working without
one. What changes is the default. It was `StubHttpClient()`, which failed on first use with a message about
missing *fixtures*, misdescribing a production job that forgot `http=`.

The new default is a small private client whose two methods raise `ConfigurationError`, saying that no HTTP
client was configured and that `http=` is how to supply one. The failure stays per-block, as an error cell,
so reports with no HTTP blocks are untouched.

[02](02-block-authoring.md) gains a short recipe showing an adapter that implements `HttpClient` and maps
status codes per the normative table in [02 §5.1](02-block-authoring.md#51-normative-error-mapping). The
`AUTO_HTML_REPORT_HTTP_BASE_URL` and `AUTO_HTML_REPORT_HTTP_API_TOKEN` settings remain for such an adapter
to read.

## 4. Versioning

The version stays a static literal in `pyproject.toml`, the only place it is written, and is changed with
`uv version --bump`. The release workflow refuses a tag that differs from it (ADR-0034). `get_version()` and
the footer are unchanged apart from the distribution name.

Versions follow [PEP 440](https://peps.python.org/pep-0440/), and the tag is the version with a `v` prefix:
`v0.1.0rc1` for `0.1.0rc1`, `v0.1.0` for `0.1.0`.

`CHANGELOG.md` is added at the repository root, in Keep a Changelog form, and updated in the same PR as any
user-visible change.

## 5. Continuous integration

`ci.yml` keeps its two jobs and their commands, and gains:

- a Python matrix of 3.11, 3.12, 3.13 and 3.14 on the checks job, with `.python-version` remaining the
  3.11 floor for local work;
- a `lowest-direct` job running the suite with `--resolution lowest-direct` on Python 3.11, which is what
  makes the floors in [§2](#2-dependency-policy) true rather than aspirational;
- a secret-scanning job over the full history, since the repository is public;
- third-party actions pinned by commit SHA, with Dependabot configured for `github-actions` and `uv` so the
  pins do not rot.

The PostgreSQL service and the wheel install test stay as they are.

## 6. The release workflow

`release.yml` runs on a pushed `v*` tag. Both GitHub environments accept only `v*` tags, and `PyPI`
requires the owner's approval.

```
tag v* ─► verify ─► checks ─► build ─► check wheel ─► TestPyPI ─► (approval) ─► PyPI ─► GitHub Release
```

1. **verify** fails unless the tag equals `v` plus the `pyproject.toml` version, and the tagged commit is on
   `main`.
2. **checks** reuses the `ci.yml` checks, so a tag cannot release what CI would reject.
3. **build** runs `uv build` once and uploads `dist/` as a workflow artifact. Every later job uses those
   files; nothing is rebuilt, so what was tested is what ships.
4. **check wheel** runs `scripts/check_install.sh` on that wheel: a clean environment, then the quick start.
5. **TestPyPI** publishes with `uv publish` in the `TestPyPI` environment.
6. **PyPI** publishes in the `PyPI` environment, and waits there for approval. Any version other than plain
   digits and dots, such as `0.1.0rc1`, stops after TestPyPI, so an unusual version fails safe.
7. **GitHub Release** is created from the tag with the `dist/` files attached, and its notes taken from the
   version's section of `CHANGELOG.md`. A pre-release gets none, because it stopped at TestPyPI.

Publishing uses trusted publishing (OIDC). No PyPI token exists anywhere, and `id-token: write` is granted
only to the two publish jobs. Each index needs a publisher registered against the repository name,
`release.yml`, and the environment name, spelled exactly `PyPI` and `TestPyPI`.

A published version cannot be replaced on either index. A bad release is yanked and a new version is cut.

### 6.1 Cutting a release

1. In a PR, run `uv version <version>`. For a final release, also rename `[Unreleased]` in `CHANGELOG.md` to
   `[<version>]` and start a new empty `[Unreleased]`. The workflow refuses a final release with no notes.
2. Merge it, then tag that commit on `main` and push the tag: `git tag v<version> && git push origin v<version>`.
3. For a final release, approve the `PyPI` deployment on the workflow run once TestPyPI has succeeded.

A failed run can be re-run: `--check-url` makes the publish jobs skip files already on the index.

## 7. Dev plan

Each stage is one PR, reviewed before the next begins.

| Stage | Delivers | Done when |
| --- | --- | --- |
| 1 | This document and ADR-0032 to ADR-0035 | Reviewed and merged. |
| 2 | The rename of §1, code and documents together, and the repository rename | CI is green and no `auto_inform` or `AUTO_INFORM_` remains outside the decision log's history and this document's §1. |
| 3 | §2 and §3, with [08](08-packaging.md) and the conventions in [README.md](README.md) rewritten to match | Floors are recorded, and a test covers the default client's error. |
| 4 | License, project metadata and classifiers, changelog, and README links made absolute for PyPI | The wheel's metadata shows them, and the README renders on TestPyPI. |
| 5 | §5 | CI is green across the matrix and on `lowest-direct`. |
| 6 | §6, then `v0.1.0rc1` on TestPyPI, then `v0.1.0` on PyPI | `pip install auto-html-report` from PyPI runs the quick start in a clean environment. |

Trusted publishers are registered between stages 2 and 6, once the repository has its final name. This
document moves to `active` when stage 6 is complete, at which point the non-goal row in
[01 §2](01-overview.md#2-non-goals-for-v1) is removed.

## Testing

- The existing audits carry the rename: `test_layering.py`, `test_doc_imports.py` and the golden test all
  fail on a missed module, export or footer token.
- A test asserts the default client raises `ConfigurationError` from both methods, naming `http=`, and that
  a report with no HTTP blocks still succeeds without one.
- A test asserts `py.typed` is in the built wheel, and `scripts/check_install.sh` imports
  `auto_html_report.testing`.
- The `lowest-direct` job is the test of §2's floors.
- `release.yml` is exercised by the release candidate. Its first real run is against TestPyPI, whose failure
  costs nothing but a version number.

## Open questions

None. The decisions above were made by the owner when this document was commissioned.

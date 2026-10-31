---
status: active
description: Distribution name, uv-managed dependency ranges, optional extras, package data, and install instructions for CI.
replaces: docs/SPEC.md §8
---

# 08 — Packaging and installation

## 1. Distribution identity

| | |
| --- | --- |
| Distribution name | `auto-html-report` |
| Import name | `auto_html_report` |
| Repository | `git@github.com:hanku4u/auto-html-report.git` |
| Requires Python | `>=3.11` (`.python-version` pinned to `3.11`) |
| Dependency manager | `uv` (ADR-0021) |
| Published to PyPI | Yes, from `0.1.0` ([12](12-publishing.md), ADR-0032) |
| Installed from | PyPI; git tag or commit SHA for unreleased commits |
| License | Apache-2.0 (ADR-0032) |

The import name has an underscore and the distribution name a hyphen. Index installs use the
distribution name, `auto-html-report[s3]`. Direct git references spell the underscore form, as below.

## 2. Dependency changes

The project uses [uv](https://docs.astral.sh/uv/) for dependency management. Runtime dependencies and
extras declare a tested floor and an upper bound at the next major version (ADR-0033). The resolved
transitive closure, with hashes, lives in the committed `uv.lock`.

> **Why ranges here and pins in the lock.** `uv.lock` governs *this repository's* development and CI
> environments. It is not shipped in the wheel and no consumer reads it. A consumer's resolver sees only
> `pyproject.toml`, and exact pins there would make the package uninstallable beside any other version of
> numpy or boto3. So the ranges keep the package installable, the lock keeps *our* builds reproducible, and
> an application that wants determinism uses its own lockfile.

**A floor is the oldest release the suite passes against on Python 3.11.** They were verified on
2026-10-31 with `--resolution lowest-direct`, including the PostgreSQL suite against a real server. They
are not support promises beyond that: a floor can be raised in any release that needs it.

### 2.1 New runtime dependencies

| Package | Range | Why | Notes |
| --- | --- | --- | --- |
| `jinja2` | `>=3.1,<4` | Renders the page shell, autoescaped by default (ADR-0010). | Single template, loaded via `importlib.resources`. |
| `matplotlib` | `>=3.7,<4` | Renders charts to PNG for embedding (ADR-0009). | Imported lazily, so HTML-only reports never load it. |
| `numpy` | `>=1.24,<3` | Numeric block data is handled as arrays: the wafer-map block types its payload as an `ndarray`. | Required rather than an extra, because the registry scan imports `wafer_map.py`, which imports numpy at module scope. |

### 2.2 New optional dependencies

| Package | Range | Extra | Why |
| --- | --- | --- | --- |
| `psycopg[binary]` | `>=3.1,<4` | `postgres` | PostgreSQL event store. |
| `boto3` | `>=1.28,<2` | `s3` | S3 output writer. Imported lazily. |

### 2.3 New development dependencies

| Package | Exact pin | Why |
| --- | --- | --- |
| `pytest` | `==9.1.1` | Test runner. |
| `ruff` | `==0.17.1` | Linting and formatting. |
| `mypy` | `==2.4.0` | Static typing; `strict` mode is required. |

Development tools keep exact pins, because they never reach a consumer. They live in the PEP 735 `dev`
dependency group, not in an extra, so they are never part of the published metadata. Any further development tool must be added to that group with an exact pin
at the moment it is introduced, not afterwards, and `uv lock` re-run in the same change.

### 2.4 Transitive dependencies

Transitive dependencies are **not** listed in this document. `uv.lock` records the fully resolved
closure, with hashes, and is committed so that CI and developer machines resolve identically.
Regenerate it with `uv lock` whenever a direct dependency changes, and never hand-edit it.

Nothing here depends on a transitive pin remaining stable, and no code imports a transitive package
directly.

### 2.5 The build backend

`[build-system] requires` uses a range — `uv_build>=0.13.0,<0.14.0` — because the build backend has to
be provisioned *before* the project's own lockfile can be honoured. This is uv's own generated
convention. It never reaches a consumer's runtime environment.

Coverage tooling is deliberately **not** included in the `dev` group. Coverage percentage is not an
enforced gate in v1 ([09 §5](09-testing-and-acceptance.md#5-ci)); the acceptance criteria are.

## 3. `pyproject.toml`

```toml
[build-system]
# A range because the build backend must be provisioned before our own lockfile can be
# honoured. uv's own generated convention.
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[project]
name = "auto-html-report"
version = "0.1.0"
description = "Dynamic HTML report generator: composable blocks, concurrent collection, one file."
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
license-files = ["LICENSE"]
authors = [{ name = "hanku4u" }]
keywords = ["html", "report", "report-generator", "matplotlib", "ci"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Text Processing :: Markup :: HTML",
    "Typing :: Typed",
]
dependencies = [
    "jinja2>=3.1,<4",
    "matplotlib>=3.7,<4",
    "numpy>=1.24,<3",
]

[project.urls]
Homepage = "https://github.com/hanku4u/auto-html-report"
Documentation = "https://github.com/hanku4u/auto-html-report/tree/main/docs"
Issues = "https://github.com/hanku4u/auto-html-report/issues"
Changelog = "https://github.com/hanku4u/auto-html-report/blob/main/CHANGELOG.md"

[project.optional-dependencies]
postgres = ["psycopg[binary]>=3.1,<4"]
s3       = ["boto3>=1.28,<2"]

[dependency-groups]
dev = [
    "pytest==9.1.1",
    "ruff==0.17.1",
    "mypy==2.4.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
markers = ["postgres: requires AUTO_HTML_REPORT_TEST_POSTGRES_DSN"]
# Lets tests import the shared builders as tests.support.*. The repository root holds no package
# named auto_html_report (that lives under src/), so this cannot shadow the installed distribution.
pythonpath = ["."]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.format]
# Ruff's formatter also formats Python snippets embedded in Markdown. docs/ is specification
# prose containing illustrative snippets, not code, and formatting them rewrites the deliberate
# comment alignment that makes them readable. This costs no lint coverage: `ruff check` does not
# lint Markdown. Without this, `uv run ruff format --check` fails on the specification itself.
exclude = ["docs", "*.md"]

[tool.mypy]
python_version = "3.11"
strict = true
# mypy has no default target: without this, a bare `uv run mypy` fails with
# "Missing target module, package, files, or command."
files = ["src", "tests", "examples"]
```

There is **no `all` extra**: consumers who want both backends spell `auto_html_report[postgres,s3]`
directly, which avoids a self-referential dependency that resolvers have no reason to accept.

## 4. `uv.lock`

`uv.lock` at the repository root records the fully resolved dependency closure — direct and
transitive — with hashes, and is committed. It exists so that CI and a developer's machine resolve to
byte-identical package sets, and so that this document has a single place to stay consistent with.

- Regenerate it with `uv lock` whenever a direct dependency changes, in the same commit as the change.
- Never hand-edit it, and never resolve a conflict inside it by choosing sides; regenerate instead.
- CI runs `uv lock --check` to fail loudly when the lockfile and `pyproject.toml` have drifted.
- It is **not** shipped in the wheel or sdist, and consumers never read it. That asymmetry is why §2
  declares ranges and leaves pinning to the lock (ADR-0033).

## 5. Two packaging details that used to cost an afternoon

1. **Templates are package data, and `uv_build` ships them automatically.** Verified against
   `uv 0.13.0`: a file at `src/auto_html_report/rendering/templates/report.html.j2` appears in the built
   wheel with no configuration at all — no `force-include`, no `artifacts` key, no
   `include_package_data`. Everything inside the module directory is packaged. The rule that still
   applies is **load it with `importlib.resources`**, never `Path(__file__).parent / "templates"`:
   that path does not exist in a zipped wheel or inside some container layers, and the failure
   appears only after deployment.
2. **The `src/` layout is mandatory, and `uv_build` infers the module name from the project name.**
   `name = "auto-html-report"` produces `src/auto_html_report/`, so the hyphen-to-underscore mapping is
   automatic — do not second-guess it with a manual `module-name` override. The layout also stops
   `import auto_html_report` from resolving to the source tree during tests, which would hide packaging
   errors until install time. CI installs the package (`uv sync --frozen`) rather than running pytest
   against the raw directory.

## 6. Installation

```bash
# from PyPI
pip install auto-html-report
uv add "auto-html-report[postgres,s3]"

# an unreleased commit, pinned to a tag or SHA
uv add "auto_html_report[s3] @ git+https://github.com/hanku4u/auto-html-report.git@<tag-or-sha>"
pip install "auto_html_report[postgres,s3] @ git+https://github.com/hanku4u/auto-html-report.git@<tag-or-sha>"
```

> **Gotcha:** uv and pip both cache git sources, so an unpinned `@main` can serve a stale package at
> the worst possible moment. Pinning a tag or SHA is the fix, not a workaround; `--refresh` (uv) and
> `--force-reinstall --no-cache-dir` (pip) are the escape hatches when you genuinely need to chase a
> moving ref. This belongs in the README as well as here.

## 7. Version resolution

The installed distribution's version is read at runtime, never stored:

```python
# src/auto_html_report/_version.py
def get_version() -> str:
    """Return the installed distribution's version, or UNKNOWN_VERSION if not installed."""
```

`pyproject.toml` is the single source of truth. Nothing in the package writes down a second copy, for
the same reason `requirements.txt` was removed: a second copy of the truth drifts (ADR-0021).
`auto_html_report.__version__` is computed once at import from `get_version()`, so the conventional attribute
a consumer reaches for cannot disagree with the installed metadata. See
ADR-0023 for the decision and what was rejected.

Two properties are load-bearing, and both were verified rather than assumed:

- **The not-installed path is real.** Reading metadata from a source tree with the package not
  installed raises `PackageNotFoundError`, so the fallback is not defensive decoration. It reports
  `0+unknown` — deliberately not a plausible release number, because a report footer should never
  claim a version that was never built.
- **The lookup is name-normalised.** Both `auto-html-report` and `auto_html_report` resolve, so the constant in
  `_version.py` cannot become a portability trap.

**Static, and checked against the tag.** ADR-0023 planned a VCS-derived version for the first release, but
`uv_build` refuses `dynamic = ["version"]`, so that would mean replacing the build backend. The version stays
a literal in `pyproject.toml`, set with `uv version --bump`, and the release workflow refuses a tag that
differs from it (ADR-0034).

## Dev plan

The packaging skeleton was stage 1 of [11-implementation-plan.md](11-implementation-plan.md). The move to
ranges, the license and the metadata are stages 3 and 4 of [12 §7](12-publishing.md#7-dev-plan), and the first
release is stage 6.

## Testing

- A packaging test job in CI installs from a clean environment using the documented command and
  imports the package, so a missing template or a `src/` layout mistake fails at install time rather
  than at first report.
- A test asserts `importlib.resources.files("auto_html_report.rendering")` can read the template. With
  `uv_build` there is no `force-include` to regress, but the resource-loading path itself still can.
- `requires-python` is asserted against the interpreter running CI, so the floor cannot drift below
  what matplotlib needs.
- A test asserts `py.typed` is present, and `scripts/check_install.sh` asserts it is in the built wheel.
- `uv lock --check` passes, and `uv sync --frozen` succeeds from the committed lockfile alone with no
  network re-resolution.

## Open questions

None. The dependency set and the Python floor are both settled; changes to either are a new change
to this document, not an open question.

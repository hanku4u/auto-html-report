---
status: proposed
description: Distribution name, uv-managed exact dependency pins, optional extras, package data, and install instructions for CI.
replaces: docs/SPEC.md §8
---

# 08 — Packaging and installation

## 1. Distribution identity

| | |
| --- | --- |
| Distribution name | `auto-inform` |
| Import name | `auto_inform` |
| Repository | `git@github.com:hanku4u/auto-inform.git` |
| Requires Python | `>=3.11` (`.python-version` pinned to `3.11`) |
| Dependency manager | `uv` (ADR-0021) |
| Published to PyPI | No (ADR-0015) |
| Installed from | git tag or commit SHA |

The import name has an underscore and the distribution name a hyphen. pip is inconsistent about
which it accepts in the extras position, so **install commands in this project always spell the
underscore form**: `auto_inform[s3]`.

## 2. Dependency changes

The project uses [uv](https://docs.astral.sh/uv/) for dependency management. Every direct dependency
below is pinned exactly with `==`, per the project's pinning convention; floating ranges are not
acceptable. The resolved transitive closure, with hashes, lives in the committed `uv.lock`. All pins
were resolved against the package index at the time of writing.

> **Why exact pins survive alongside a lockfile.** `uv.lock` governs *this repository's* development
> and CI environments. It is not shipped in the wheel and no consumer reads it: a package installed
> from git resolves against `pyproject.toml` alone. So the lock makes *our* builds reproducible, while
> the `==` pins are the only thing that makes *consumers'* installs deterministic. Keep both. See
> ADR-0021.

### 2.1 New runtime dependencies

| Package | Exact pin | Why | Notes |
| --- | --- | --- | --- |
| `jinja2` | `==3.1.6` | Renders the page shell, autoescaped by default (ADR-0010). | Single template, loaded via `importlib.resources`. |
| `matplotlib` | `==3.11.2` | Renders charts to PNG for embedding (ADR-0009). | Imported lazily, so HTML-only reports never load it. Its own `requires_python` is `>=3.11`, which independently confirms the Python floor. |
| `numpy` | `==2.4.6` | Numeric block data is handled as arrays: the wafer-map block types its payload as an `ndarray`. | Was already present transitively through matplotlib, which requires `>=1.23`. Pinned explicitly now that a block imports it directly, because importing an undeclared transitive dependency is how a dependency set rots. |

### 2.2 New optional dependencies

| Package | Exact pin | Extra | Why |
| --- | --- | --- | --- |
| `psycopg[binary]` | `==3.3.6` | `postgres` | PostgreSQL event store. |
| `boto3` | `==1.43.114` | `s3` | S3 output writer. Imported lazily. |

### 2.3 New development dependencies

| Package | Exact pin | Why |
| --- | --- | --- |
| `pytest` | `==9.1.1` | Test runner. |
| `ruff` | `==0.17.1` | Linting and formatting. |
| `mypy` | `==2.4.0` | Static typing; `strict` mode is required. |

Development tools live in the PEP 735 `dev` dependency group, not in an extra, so they are never part
of the published metadata. Any further development tool must be added to that group with an exact pin
at the moment it is introduced, not afterwards, and `uv lock` re-run in the same change.

### 2.4 Transitive dependencies

Transitive dependencies are **not** listed in this document. `uv.lock` records the fully resolved
closure, with hashes, and is committed so that CI and developer machines resolve identically.
Regenerate it with `uv lock` whenever a direct pin changes, and never hand-edit it.

Nothing here depends on a transitive pin remaining stable, and no code imports a transitive package
directly.

### 2.5 The one deliberate range

`[build-system] requires` uses a range — `uv_build>=0.13.0,<0.14.0` — because the build backend has to
be provisioned *before* the project's own lockfile can be honoured. This is uv's own generated
convention and the only non-exact `requires` in the project. It never reaches a consumer's runtime
environment.

Coverage tooling is deliberately **not** included in the `dev` group. Coverage percentage is not an
enforced gate in v1 ([09 §5](09-testing-and-acceptance.md#5-ci)); the acceptance criteria are.

## 3. `pyproject.toml`

```toml
[build-system]
# The one deliberate range in the project: the build backend must be provisioned
# before our own lockfile can be honoured. uv's own generated convention.
requires = ["uv_build>=0.13.0,<0.14.0"]
build-backend = "uv_build"

[project]
name = "auto-inform"
version = "0.1.0"
description = "Dynamic HTML report generator: composable blocks, concurrent collection, one file."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "jinja2==3.1.6",
    "matplotlib==3.11.2",
    "numpy==2.4.6",
]

[project.optional-dependencies]
postgres = ["psycopg[binary]==3.3.6"]
s3       = ["boto3==1.43.114"]

[dependency-groups]
dev = [
    "pytest==9.1.1",
    "ruff==0.17.1",
    "mypy==2.4.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
markers = ["postgres: requires AUTO_INFORM_TEST_POSTGRES_DSN"]
# Lets tests import the shared builders as tests.support.*. The repository root holds no package
# named auto_inform (that lives under src/), so this cannot shadow the installed distribution.
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
files = ["src", "tests"]
```

There is **no `all` extra**: consumers who want both backends spell `auto_inform[postgres,s3]`
directly, which avoids a self-referential dependency that resolvers have no reason to accept.

## 4. `uv.lock`

`uv.lock` at the repository root records the fully resolved dependency closure — direct and
transitive — with hashes, and is committed. It exists so that CI and a developer's machine resolve to
byte-identical package sets, and so that this document has a single place to stay consistent with.

- Regenerate it with `uv lock` whenever a direct pin changes, in the same commit as the change.
- Never hand-edit it, and never resolve a conflict inside it by choosing sides; regenerate instead.
- CI runs `uv lock --check` to fail loudly when the lockfile and `pyproject.toml` have drifted.
- It is **not** shipped in the wheel or sdist, and consumers never read it. That asymmetry is why §2
  keeps exact pins rather than delegating pinning to the lock (ADR-0021).

## 5. Two packaging details that used to cost an afternoon

1. **Templates are package data, and `uv_build` ships them automatically.** Verified against
   `uv 0.13.0`: a file at `src/auto_inform/rendering/templates/report.html.j2` appears in the built
   wheel with no configuration at all — no `force-include`, no `artifacts` key, no
   `include_package_data`. Everything inside the module directory is packaged. The rule that still
   applies is **load it with `importlib.resources`**, never `Path(__file__).parent / "templates"`:
   that path does not exist in a zipped wheel or inside some container layers, and the failure
   appears only after deployment.
2. **The `src/` layout is mandatory, and `uv_build` infers the module name from the project name.**
   `name = "auto-inform"` produces `src/auto_inform/`, so the hyphen-to-underscore mapping is
   automatic — do not second-guess it with a manual `module-name` override. The layout also stops
   `import auto_inform` from resolving to the source tree during tests, which would hide packaging
   errors until install time. CI installs the package (`uv sync --frozen`) rather than running pytest
   against the raw directory.

## 6. Installation

```bash
# into a uv-managed project, pinned to a tag or commit SHA
uv add "auto_inform[s3] @ git+https://github.com/hanku4u/auto-inform.git@v0.1.0"

# latest from main (unpinned — expect surprises)
uv add "auto_inform @ git+https://github.com/hanku4u/auto-inform.git"

# plain pip works too, and still needs the underscore form
pip install "auto_inform[postgres,s3] @ git+https://github.com/hanku4u/auto-inform.git@v0.1.0"
```

> **Gotcha:** uv and pip both cache git sources, so an unpinned `@main` can serve a stale package at
> the worst possible moment. Pinning a tag or SHA is the fix, not a workaround; `--refresh` (uv) and
> `--force-reinstall --no-cache-dir` (pip) are the escape hatches when you genuinely need to chase a
> moving ref. This belongs in the README as well as here.

A tag is not created by this work. `v0.1.0` is the intended first tag; until it exists, pin a commit
SHA.

## 7. Version resolution

The installed distribution's version is read at runtime, never stored:

```python
# src/auto_inform/_version.py
def get_version() -> str:
    """Return the installed distribution's version, or UNKNOWN_VERSION if not installed."""
```

`pyproject.toml` is the single source of truth. Nothing in the package writes down a second copy, for
the same reason `requirements.txt` was removed: a second copy of the truth drifts (ADR-0021). See
ADR-0023 for the decision and what was rejected.

Two properties are load-bearing, and both were verified rather than assumed:

- **The not-installed path is real.** Reading metadata from a source tree with the package not
  installed raises `PackageNotFoundError`, so the fallback is not defensive decoration. It reports
  `0+unknown` — deliberately not a plausible release number, because a report footer should never
  claim a version that was never built.
- **The lookup is name-normalised.** Both `auto-inform` and `auto_inform` resolve, so the constant in
  `_version.py` cannot become a portability trap.

**Deferred, with a trigger.** A VCS-derived version (`uv-dynamic-versioning` or `hatch-vcs`, with
`dynamic = ["version"]`) is the better long-term answer, because it makes the git tag *be* the version
and drift structurally impossible — which suits ADR-0015's install-from-a-tag model exactly. Adopt it
at the first release, or the first time a tag and `pyproject.toml` disagree, whichever comes first.
Until then it is scaffolding around a release process that does not exist, and it would add a pinned
build-time dependency needing its own entry in §2.

## Testing

- A packaging test job in CI installs from a clean environment using the documented command and
  imports the package, so a missing template or a `src/` layout mistake fails at install time rather
  than at first report.
- A test asserts `importlib.resources.files("auto_inform.rendering")` can read the template. With
  `uv_build` there is no `force-include` to regress, but the resource-loading path itself still can.
- `requires-python` is asserted against the interpreter running CI, so the floor cannot drift below
  what matplotlib needs.
- `uv lock --check` passes, and `uv sync --frozen` succeeds from the committed lockfile alone with no
  network re-resolution.

## Open questions

None. The dependency set and the Python floor are both settled; changes to either are a new change
to this document, not an open question.

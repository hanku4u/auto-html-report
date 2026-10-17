---
status: proposed
description: Distribution name, exact dependency pins, optional extras, package data, and install instructions for CI.
replaces: docs/SPEC.md §8
---

# 08 — Packaging and installation

## 1. Distribution identity

| | |
| --- | --- |
| Distribution name | `auto-inform` |
| Import name | `auto_inform` |
| Repository | `git@github.com:hanku4u/auto-inform.git` |
| Requires Python | `>=3.11` |
| Published to PyPI | No (ADR-0015) |
| Installed from | git tag or commit SHA |

The import name has an underscore and the distribution name a hyphen. pip is inconsistent about
which it accepts in the extras position, so **install commands in this project always spell the
underscore form**: `auto_inform[s3]`.

## 2. Dependency changes

This document introduces the project's dependency set. Every pin below is exact, per the project's
pinning convention; floating ranges are not acceptable. All of the runtime pins were resolved
against the package index at the time of writing.

### 2.1 New runtime dependencies

| Package | Exact pin | Why | Notes |
| --- | --- | --- | --- |
| `jinja2` | `==3.1.6` | Renders the page shell, autoescaped by default (ADR-0010). | Single template, loaded via `importlib.resources`. |
| `matplotlib` | `==3.11.2` | Renders charts to PNG for embedding (ADR-0009). | Imported lazily, so HTML-only reports never load it. Its own `requires_python` is `>=3.11`, which independently confirms the Python floor. |

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

Any further development tool must be added to both `pyproject.toml` and `requirements.txt` with an
exact pin at the moment it is introduced, not afterwards.

### 2.4 Transitive dependencies

Transitive dependencies are **not** pinned in this document, and they are not yet recorded in
`requirements.txt`: that file currently carries the top-level pins only, with the closure to be
appended by `pip freeze` once the development environment is first built — see the note at the foot
of [../requirements.txt](../requirements.txt). Once generated, the closure is committed and reviewed
as part of that change. Nothing here depends on a transitive pin remaining stable, and no code
imports a transitive package directly.

Coverage tooling is deliberately **not** included in the `dev` extra. Coverage percentage is not an
enforced gate in v1 ([09 §5](09-testing-and-acceptance.md#5-ci)); the acceptance criteria are.

## 3. `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "auto-inform"
version = "0.1.0"
description = "Dynamic HTML report generator: composable blocks, concurrent collection, one file."
requires-python = ">=3.11"
dependencies = [
    "jinja2==3.1.6",
    "matplotlib==3.11.2",
]

[project.optional-dependencies]
postgres = ["psycopg[binary]==3.3.6"]
s3       = ["boto3==1.43.114"]
all      = ["auto_inform[postgres,s3]"]
dev      = ["pytest==9.1.1", "ruff==0.17.1", "mypy==2.4.0"]

[tool.hatch.build.targets.wheel]
packages = ["src/auto_inform"]

[tool.hatch.build.targets.wheel.force-include]
"src/auto_inform/rendering/templates" = "auto_inform/rendering/templates"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
markers = ["postgres: requires AUTO_INFORM_TEST_POSTGRES_DSN"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
```

## 4. `requirements.txt`

`requirements.txt` at the repository root records the fully-pinned environment for CI: runtime,
optional and development dependencies together, each with a comment naming its group. It exists so
that a CI run is reproducible and so that this document has a single place to be consistent with.

Consumers installing the package do **not** use it — they use the package's own extras, which carry
the same pins. See [../requirements.txt](../requirements.txt).

## 5. Two packaging details that will otherwise cost an afternoon

1. **Templates are package data.** The Jinja2 shell must be included in the wheel (`force-include`
   above, or hatchling's `artifacts`/`include` — note the American spelling there, because that is
   the tool's actual key name), and loaded with `importlib.resources`, never
   `Path(__file__).parent / "templates"`. That path does not exist in a zipped wheel or inside some
   container layers, and the failure appears only after deployment.
2. **`src/` layout is mandatory.** It prevents `import auto_inform` from resolving to the source tree
   during tests, which hides packaging errors until install time. CI must install the package
   (`pip install -e ".[dev]"`) rather than run pytest against the raw directory.

## 6. Installation

```bash
# latest from main
pip install "auto_inform @ git+https://github.com/hanku4u/auto-inform.git"

# with optional backends
pip install "auto_inform[postgres,s3] @ git+https://github.com/hanku4u/auto-inform.git"

# recommended for CI: pin to a tag or commit SHA for reproducible builds
pip install "auto_inform[s3] @ git+https://github.com/hanku4u/auto-inform.git@v0.1.0"
```

> **Gotcha:** pip caches git checkouts. When re-installing an unpinned `@main` in CI you may get a
> stale copy — pin a tag or SHA, or pass `--force-reinstall --no-cache-dir`. This has burned every
> team that has ever done this, and it belongs in the README as well as here.

A tag is not created by this work. `v0.1.0` is the intended first tag; until it exists, pin a commit
SHA.

## Testing

- A packaging test job in CI installs from a clean virtualenv using the documented command and
  imports the package, so a missing template or a `src/` layout mistake fails at install time rather
  than at first report.
- A test asserts `importlib.resources.files("auto_inform.rendering")` can read the template, which
  catches a `force-include` regression without needing a wheel build.
- `requires-python` is asserted against the interpreter running CI, so the floor cannot drift below
  what matplotlib needs.

## Open questions

None. The dependency set and the Python floor are both settled; changes to either are a new change
to this document, not an open question.

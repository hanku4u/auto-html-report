# auto_inform

Generates self-contained HTML reports from composable blocks: one table, three columns —
Item, Data, Check — with each row contributed by a small block that knows how to gather one
piece of information and render it.

**Status: pre-implementation.** Only stage 1 of
[`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) is complete, so this
repository currently builds, lints, type-checks and tests cleanly, but contains no report
generator yet. The specification is the authority on what it will do.

## Where to look

| If you want… | Read |
| --- | --- |
| What the tool will do | [`docs/01-overview.md`](docs/01-overview.md) |
| The specification as a whole | [`docs/README.md`](docs/README.md) — the index and conventions |
| Why a design decision was made | [`docs/10-decision-log.md`](docs/10-decision-log.md) |
| What to build next | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) |

## Development

Requires [uv](https://docs.astral.sh/uv/). It will provision the right Python interpreter
from `.python-version`; you do not need one pre-installed.

```bash
uv sync --frozen            # install the pinned environment
uv run pytest               # tests
uv run ruff check           # lint
uv run ruff format --check  # formatting
uv run mypy                 # types, strict via pyproject.toml
```

Dependencies are declared with exact pins in `pyproject.toml` and resolved in the committed
`uv.lock`. If you change a dependency, run `uv lock` in the same commit — CI runs
`uv lock --check` and will fail if the two have drifted.

Install and usage instructions for consumers arrive with the implementation; the intended
shape is in [`docs/08-packaging.md`](docs/08-packaging.md).

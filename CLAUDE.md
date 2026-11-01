# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`auto_html_report` is a Python library that turns a list of `Block`s into one self-contained HTML report: inline
CSS, charts as base64 PNG data URIs, no JavaScript, no external requests. It's Apache-2.0 and on PyPI. The
distribution is `auto-html-report`; the import name is `auto_html_report`.

**Keep it as simple as possible while keeping good fundamentals.** That is the owner's standing request. Prefer plain
functions and dicts, and don't add a layer, setting or abstraction for a need that doesn't exist yet. Event discovery
and processing were removed on purpose and live at the `v0.1.0` tag.

## Commands

The environment is managed by uv. CI (`.github/workflows/ci.yml`) runs these on Python 3.11 to 3.14, plus a run at
the dependency floors, the wheel install check and a history secret scan:

```bash
uv sync --frozen --all-extras
uv lock --check
uv run ruff check
uv run ruff format --check
uv run mypy                     # strict; covers src, tests, examples and scripts
uv run pytest

uv run pytest tests/test_build_report.py -k retry   # a subset
./scripts/check_install.sh                          # build the wheel, install it, run the quick start

uv run python examples/quick_start.py /tmp/report-demo
uv run python -m examples.manufacturing
uv run python -m scripts.shared_data_example
uv run python -m examples.plumbing_check            # real HTTP against a local API; also a CI step
```

Releases are tag-driven: see `docs/publishing.md`. Don't rename `release.yml` or the `PyPI` and `TestPyPI` environments
without updating the trusted publishers on both indexes.

## Architecture

`src/auto_html_report/docs/design.md` has the pipeline and the reasons for each decision. Read it before changing behaviour.

- `block.py`: `Block`. `gather` names a function, and `render` returns the Data cell's HTML.
- `report.py`: `build_report` (check, plan, fetch, render) and `Report`.
- `rendering.py` and `templates/`: `escape`, `html_table`, `figure_to_data_uri` and the document.
- `errors.py`: the exceptions.

## Rules that are easy to break

- **Missing context and failed fetches raise; only `render` failures become error rows.**
- **Error messages name context keys, never values**, which can be credentials.
- **Gather functions set their own network timeouts**, because a running thread can't be interrupted.
- **Read `gather` through the class** (`type(self).gather`): through `self`, Python binds it as a method.
- **`src/auto_html_report/docs/README.md` is a symlink to the root `README.md`,** so the guide ships in the wheel. Edit
  the root file. The build turns the link into a real copy.
- **Every Python block in `README.md` is executed by `tests/test_readme.py`**, in order and sharing a namespace.
- **`import auto_html_report` must not load matplotlib**, which is an optional extra. A test pins this.
- **Anything from outside the process goes through `escape` or `html_table`.** `row.html | safe` is the template's only
  unescaped value.

## Tests

- No network and no real waiting: use `retry_delay=0`, and events rather than sleeps. Real HTTP belongs in
  `examples/plumbing_check.py`.
- Name tests `test_<setup>_should_<outcome>`, with `# given / # when / # then` comments.
- An audit of other code needs a negative control, as `test_the_audit_should_flag_an_external_load` is.

#!/usr/bin/env bash
# Build the wheel, install it into a scratch environment, and run the README's quick start from it.
#
# The point is to catch what a source-tree test cannot: a template missing from the wheel, a package
# directory the build backend did not infer, an import that only resolves because the repository is the
# working directory. CI's packaging job runs exactly this script, and the README quotes the same commands
# a developer runs by hand.
#
# With a path, checks that wheel instead of building one: the release workflow passes the file it will publish.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# Resolved before the `cd`, because a relative path means the caller's directory.
if [ $# -gt 0 ]; then
    wheel="$(realpath "$1")"
    cd "$root"
else
    cd "$root"
    uv build --wheel --out-dir "$scratch/dist" >/dev/null
    wheel="$(echo "$scratch"/dist/*.whl)"
fi
uv venv "$scratch/venv" >/dev/null
python="$scratch/venv/bin/python"
uv pip install --python "$python" "$wheel" >/dev/null

PYTHONPATH= "$python" -c "
import auto_html_report
from importlib.resources import files
assert files('auto_html_report').joinpath('py.typed').is_file(), 'py.typed is missing from the wheel'
assert files('auto_html_report').joinpath('templates', 'report.css').is_file(), 'templates are missing'
docs = files('auto_html_report').joinpath('docs')
assert 'build_report' in docs.joinpath('README.md').read_text(encoding='utf-8'), 'the guide is missing'
assert docs.joinpath('design.md').is_file(), 'the design notes are missing'
print(f'imported {auto_html_report.__version__}')
"

# The quick start is copied out of the repository and run in an empty directory: if it still works, the
# installed distribution is what it used, not the source tree.
cp examples/quick_start.py "$scratch/quick_start.py"
cd "$scratch"
# An empty PYTHONPATH: a developer's environment must not shadow the wheel under test.
PYTHONPATH= "$python" quick_start.py out >/dev/null

# `|| true`: with `set -e`, a missing `out/` would abort on find's non-zero status before the message below.
report="$(find out -name '*.html' -print -quit 2>/dev/null || true)"
if [ -z "$report" ]; then
    echo "the quick start wrote no report" >&2
    exit 1
fi
# The title proves the shell rendered; the table content proves the block's data cell did too.
if ! grep -q "Quick start" "$report" || ! grep -q "Deployment" "$report"; then
    echo "the report is missing its title or its row" >&2
    exit 1
fi
echo "ok: the installed wheel generated $(basename "$report")"

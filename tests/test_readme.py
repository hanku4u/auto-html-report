"""Every Python example in the README runs, in order, as a reader would follow them."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"


class FakeClient:
    """Stands in for the ``my_client`` the README assumes you already have."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, path: str, *, timeout: float) -> dict[str, Any]:
        self.calls.append(path)
        return {"records": [{"date": "2026-10-12", "engineer": "A. Rivera"}]}


def _python_examples() -> list[str]:
    return re.findall(
        r"^```python\n(.*?)^```$", README.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL
    )


def _run_examples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run each example in a scratch directory, sharing one namespace as a reader would."""
    monkeypatch.chdir(tmp_path)
    namespace: dict[str, Any] = {"my_client": FakeClient()}
    for example in _python_examples():
        exec(compile(example, str(README), "exec"), namespace)  # noqa: S102 - our own README
    return namespace


def test_the_readme_examples_should_all_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given the README's examples
    assert len(_python_examples()) >= 10, "the pattern should find the README's examples"

    # when each one runs
    namespace = _run_examples(tmp_path, monkeypatch)

    # then the quick start wrote a file, and the example test passes
    assert list((tmp_path / "reports").glob("*.html"))
    namespace["test_maintenance_count_shows_the_number_of_records"]()


def test_the_readme_blocks_should_share_one_call_and_all_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given the blocks the README defines, all reading maintenance data
    namespace = _run_examples(tmp_path, monkeypatch)
    client = namespace["my_client"]
    client.calls.clear()
    blocks = [
        namespace[name]()
        for name in (
            "MaintenanceTable",
            "MaintenanceCount",
            "ToolSummary",
            "ToolNote",
            "WorkPerEngineer",
        )
    ]
    blocks.append(namespace["RecentMaintenance"]("T-104"))

    # when they go into one report
    report = namespace["build_report"]("Together", blocks, {"tool_id": "T-104", "http": client})

    # then the API was called once, and nothing failed to render
    assert client.calls == ["/maintenance/T-104"]
    assert report.failed_rows == ()

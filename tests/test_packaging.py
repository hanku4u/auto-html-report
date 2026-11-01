"""What an installed package must provide, and the examples still running."""

from __future__ import annotations

import tomllib
from importlib.metadata import metadata, version
from importlib.resources import files
from pathlib import Path

import pytest

import auto_html_report
from examples import quick_start


def test_every_public_name_should_exist() -> None:
    # given the declared public surface
    # when each name is resolved
    # then none is stale
    for name in auto_html_report.__all__:
        assert hasattr(auto_html_report, name), name


def test_the_version_should_match_pyproject() -> None:
    # given the one place the version is written
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    # when it is read back
    # then the metadata and the attribute agree
    assert version("auto-html-report") == declared
    assert auto_html_report.__version__ == declared


def test_the_package_should_carry_its_typing_marker_and_templates() -> None:
    # given the installed package
    package = files("auto_html_report")

    # then its non-Python files are there
    assert package.joinpath("py.typed").is_file()
    assert package.joinpath("templates", "report.html.j2").is_file()
    assert package.joinpath("templates", "report.css").is_file()


def test_the_package_should_carry_its_documentation() -> None:
    # given the installed package, which a coding agent can read without internet access
    docs = files("auto_html_report").joinpath("docs")

    # then the guide matches the root README, and the design notes are there
    root_readme = Path(__file__).resolve().parents[1] / "README.md"
    assert docs.joinpath("README.md").read_text(encoding="utf-8") == root_readme.read_text(
        encoding="utf-8"
    )
    assert docs.joinpath("design.md").is_file()


def test_the_metadata_should_carry_what_a_pypi_page_needs() -> None:
    # given the installed metadata
    declared = metadata("auto-html-report")

    # then the license and links are there, and no license classifier sits beside the expression
    assert declared["License-Expression"] == "Apache-2.0"
    assert declared["Requires-Python"] == ">=3.11"
    classifiers = declared.get_all("Classifier", [])
    assert "Typing :: Typed" in classifiers
    assert not any(classifier.startswith("License ::") for classifier in classifiers)


def test_the_quick_start_should_write_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # given the code the README shows
    # when it runs
    path = quick_start.main(tmp_path)

    # then one document exists with its row in it
    html = path.read_text(encoding="utf-8")
    assert "Quick start" in html
    assert "Deployment" in html
    assert str(path) in capsys.readouterr().out

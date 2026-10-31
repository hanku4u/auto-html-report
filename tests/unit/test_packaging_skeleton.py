"""Stage 1 packaging checks, plus the public surface the block-authoring examples import.

These are deliberately shallow. They prove the packaging skeleton is wired up correctly -- the wheel
builds, the package is importable after installation, the declared version reaches the installed
metadata, and the names the docs tell block authors to import actually resolve.
"""

import subprocess
import sys
from importlib.metadata import metadata, version
from importlib.resources import files

import pytest

import auto_html_report

# The exact line docs/06-output-html.md §4 gives to block authors. Imported at module scope on purpose:
# if the documented import breaks, this file fails to collect rather than failing one assertion.
# The configuration factories are imported at module scope as well, so that removing one from the public
# surface fails test collection rather than a consumer's job script. `docs/05` §6 imports these names from
# the root, and the module docstring promises them.
# docs/03 §2 calls collect_blocks, generate_report and render_report "the three public entry points".
# Imported at module scope so removing one from the surface fails collection, not one assertion.
from auto_html_report import (
    build_output_writer,
    collect_blocks,
    configure_logging,
    escape,
    figure_lock,
    figure_to_data_uri,
    generate_report,
    html_table,
    load_settings,
    new_discovery_context,
    new_report_context,
    render_report,
)


def test_importing_the_package_should_expose_the_block_authoring_surface() -> None:
    # given a package installed from the pinned environment
    # when it is imported
    # then the names stage 4's blocks will import from the documented examples are present
    expected = {
        "Block",
        "BlockStatus",
        "BlockOutcome",
        "ReportContext",
        "RenderContext",
        "escape",
        "html_table",
        "figure_to_data_uri",
        "figure_lock",
        "TransientBlockError",
        "PermanentBlockError",
    }
    assert expected <= set(auto_html_report.__all__)


def test_the_public_surface_should_declare_everything_it_exposes() -> None:
    # given the declared public surface
    # when each name is resolved
    # then every declared export exists, and the list has no duplicate entries -- a stale name here is
    # an ImportError waiting for whoever copies an example
    assert len(auto_html_report.__all__) == len(set(auto_html_report.__all__))
    for name in auto_html_report.__all__:
        assert hasattr(auto_html_report, name), name


def test_the_ports_a_consumer_implements_should_be_importable_from_the_root() -> None:
    # given the protocols a consumer writes an adapter against (ADR-0035)
    # then they are public, rather than reachable only through a private path
    assert {"Clock", "HttpClient", "EventStore", "OutputWriter"} <= set(auto_html_report.__all__)


def test_the_testing_module_should_expose_the_stub_client() -> None:
    # given the module a consumer's tests import
    from auto_html_report.testing import StubHttpClient, StubNotConfiguredError

    # then an unknown path raises the documented error, so a consumer's test fails loudly
    with pytest.raises(StubNotConfiguredError):
        StubHttpClient().get_json("/missing", timeout=1.0)


def test_the_package_should_carry_the_typing_marker() -> None:
    # given the installed package
    # then py.typed is present: without it a consumer's type checker may ignore these annotations
    assert files("auto_html_report").joinpath("py.typed").is_file()


def test_the_import_advertised_in_the_specification_should_be_callable() -> None:
    # given the helpers docs/06 §4 documents for block authors
    # then they resolve and are callable, rather than failing in a block author's first five minutes
    assert callable(escape)
    assert callable(html_table)
    assert callable(figure_to_data_uri)
    assert callable(figure_lock)


def test_the_configuration_factories_should_be_importable_from_the_root() -> None:
    # given the names a job script imports, as documented in `docs/05` §6 and promised by the package
    # docstring
    # when they are used
    # then they are callable. The module-scope import above is the real guard -- it fails collection rather
    # than a test -- and this asserts the shape of what was imported
    for factory in (
        build_output_writer,
        configure_logging,
        load_settings,
        new_discovery_context,
        new_report_context,
    ):
        assert callable(factory)


def test_installed_distribution_metadata_should_report_the_declared_version() -> None:
    # given the package installed from pyproject.toml rather than resolved from the source tree
    # when the installed metadata is read
    # then the version matches the declaration, which only holds if the build backend ran
    assert version("auto-html-report") == "0.1.0"


def test_installed_distribution_metadata_should_require_python_three_eleven_or_newer() -> None:
    # given the interpreter running the test suite
    # when the declared requirement is read
    # then the floor is at least 3.11, which is what matplotlib 3.11 needs
    requires_python = metadata("auto-html-report")["Requires-Python"]
    assert requires_python == ">=3.11"


def test_installed_distribution_metadata_should_carry_what_a_pypi_page_needs() -> None:
    # given the installed distribution's metadata
    declared = metadata("auto-html-report")

    # then the license and the links reach it, and no license classifier sits beside the expression,
    # which PyPI rejects on upload
    assert declared["License-Expression"] == "Apache-2.0"
    assert "LICENSE" in declared.get_all("License-File", [])
    assert any(url.startswith("Issues, ") for url in declared.get_all("Project-URL", []))
    classifiers = declared.get_all("Classifier", [])
    assert "Typing :: Typed" in classifiers
    assert not any(classifier.startswith("License ::") for classifier in classifiers)


def test_importing_the_package_should_not_import_the_optional_backends() -> None:
    # given a fresh interpreter and the base install (no extras imported by anything)
    # when the package is imported
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, auto_html_report; print('boto3' in sys.modules, 'psycopg' in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # then neither optional driver is loaded: a block author who only renders tables does not pay for
    # them, which is what ADR-0031 promises
    assert completed.stdout.strip() == "False False"


def test_the_three_documented_entry_points_should_be_importable_from_the_root() -> None:
    # given the three entry points docs/03 §2 promises: the composition, and the two lower-level steps a
    # caller uses to inspect outcomes before writing
    # when they are imported from the root (the module-scope import above is the real guard)
    # then all three are callable, so a caller does not have to reimplement the pipeline
    for entry_point in (collect_blocks, generate_report, render_report):
        assert callable(entry_point)

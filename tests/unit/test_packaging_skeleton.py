"""Stage 1 packaging checks, plus the public surface the block-authoring examples import.

These are deliberately shallow. They prove the packaging skeleton is wired up correctly -- the wheel
builds, the package is importable after installation, the declared version reaches the installed
metadata, and the names the docs tell block authors to import actually resolve.
"""

import subprocess
import sys
from importlib.metadata import metadata, version

import auto_inform

# The exact line docs/06-output-html.md §4 gives to block authors. Imported at module scope on purpose:
# if the documented import breaks, this file fails to collect rather than failing one assertion.
# The configuration factories are imported at module scope as well, so that removing one from the public
# surface fails test collection rather than a consumer's job script. `docs/05` §6 imports these names from
# the root, and the module docstring promises them.
# docs/03 §2 calls collect_blocks, generate_report and render_report "the three public entry points".
# Imported at module scope so removing one from the surface fails collection, not one assertion.
from auto_inform import (
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
    assert expected <= set(auto_inform.__all__)


def test_the_public_surface_should_declare_everything_it_exposes() -> None:
    # given the declared public surface
    # when each name is resolved
    # then every declared export exists, and the list has no duplicate entries -- a stale name here is
    # an ImportError waiting for whoever copies an example
    assert len(auto_inform.__all__) == len(set(auto_inform.__all__))
    for name in auto_inform.__all__:
        assert hasattr(auto_inform, name), name


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
    assert version("auto-inform") == "0.1.0"


def test_installed_distribution_metadata_should_require_python_three_eleven_or_newer() -> None:
    # given the interpreter running the test suite
    # when the declared requirement is read
    # then the floor is at least 3.11, which is what matplotlib 3.11 needs
    requires_python = metadata("auto-inform")["Requires-Python"]
    assert requires_python == ">=3.11"


def test_importing_the_package_should_not_import_the_optional_backends() -> None:
    # given a fresh interpreter and the base install (no extras imported by anything)
    # when the package is imported
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, auto_inform; print('boto3' in sys.modules, 'psycopg' in sys.modules)",
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

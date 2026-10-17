"""Stage 1 packaging checks.

These are deliberately shallow. They exist to prove the packaging skeleton is wired up
correctly — the wheel builds, the package is importable after installation, and the declared
version reaches the installed metadata — not to test behaviour that does not exist yet.
"""

from importlib.metadata import version

import auto_inform


def test_import_auto_inform_with_clean_install_should_expose_no_public_api_yet() -> None:
    # given a package installed from the pinned environment
    # when it is imported (above)
    # then it deliberately exports nothing until stage 3 of the implementation plan
    assert auto_inform.__all__ == []


def test_installed_distribution_metadata_should_report_the_declared_version() -> None:
    # given the package installed from pyproject.toml rather than resolved from the source tree
    # when the installed metadata is read
    # then the version matches the declaration, which only holds if the build backend ran
    assert version("auto-inform") == "0.1.0"


def test_installed_distribution_metadata_should_require_python_three_eleven_or_newer() -> None:
    # given the interpreter running the test suite
    # when the declared requirement is read
    # then the floor is at least 3.11, which is what matplotlib 3.11 needs
    from importlib.metadata import metadata

    requires_python = metadata("auto-inform")["Requires-Python"]
    assert requires_python == ">=3.11"

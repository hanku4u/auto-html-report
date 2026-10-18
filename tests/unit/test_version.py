"""Resolution of the package version, including the not-installed path.

The second test is the one that matters: the fallback is only worth having if it actually fires, and
that path is unreachable by normal means because the test suite runs against an installed package.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import pytest

import auto_inform
from auto_inform import _version


def test_get_version_with_installed_distribution_should_agree_with_the_distribution_metadata() -> (
    None
):
    # given a package installed from pyproject.toml
    # when the version is resolved at runtime
    # then it agrees with the installed metadata, which is what makes pyproject.toml the single
    # source of truth: there is no second copy that could disagree
    assert _version.get_version() == version("auto-inform")


def test_get_version_without_installed_metadata_should_report_unknown_rather_than_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given a source tree where the distribution is not installed -- verified to raise, not
    # hypothetical (see docs/08-packaging.md §7)
    def _raise_for_missing_distribution(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(_version, "version", _raise_for_missing_distribution)

    # when the version is resolved
    # then a placeholder is reported rather than a number that looks authoritative
    assert _version.get_version() == _version.UNKNOWN_VERSION


def test_unknown_version_should_be_visibly_unknown() -> None:
    # given the placeholder used when the package is not installed
    # then it says so in words, because a reader of a report footer should never be shown a version
    # that was never built
    assert "unknown" in _version.UNKNOWN_VERSION


def test_the_package_should_expose_the_version_it_resolves() -> None:
    # given the conventional `__version__` attribute a consumer reaches for
    # then it is computed from the resolver, never a hard-coded copy that could drift
    assert auto_inform.__version__ == _version.get_version()

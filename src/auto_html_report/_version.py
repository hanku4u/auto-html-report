"""The installed version. ``pyproject.toml`` is the only place it is written."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    try:
        return version("auto-html-report")
    except PackageNotFoundError:
        return "0+unknown"

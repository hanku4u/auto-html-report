"""The test suite.

Marked as a package so that ``tests.support`` imports under one name for every tool: without this,
mypy sees ``tests/support/builders.py`` both as ``support.builders`` and as ``tests.support.builders``
and refuses to continue.
"""

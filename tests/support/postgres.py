"""Test-only helpers for the optional PostgreSQL backend.

The suite runs in a **dedicated schema** (``auto_inform_test``), never the public one. The shared cases
drop the table to start from an empty store, and doing that in whatever database
``AUTO_INFORM_TEST_POSTGRES_DSN`` happens to name could wipe a developer's real queue — the DSN is the
operator's, the schema is this suite's.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import pytest

#: Owned by the test suite; dropped and recreated as needed.
TEST_SCHEMA = "auto_inform_test"

_DSN_VARIABLE = "AUTO_INFORM_TEST_POSTGRES_DSN"


def postgres_test_dsn(*, timezone: str | None = None) -> str:
    """A DSN for the test schema with an empty ``events`` table.

    Skips when ``AUTO_INFORM_TEST_POSTGRES_DSN`` is not set. Once it *is* set, psycopg is imported
    normally rather than skipped: a configured database with no driver is a broken environment, not a
    reason to pass quietly.
    """
    base = os.environ.get(_DSN_VARIABLE)
    if not base:
        pytest.skip(f"{_DSN_VARIABLE} is not set")

    import psycopg

    options = f"-c search_path={TEST_SCHEMA}"
    if timezone is not None:
        # Also the control for the UTC normalisation: a non-UTC session decodes TIMESTAMPTZ in its own
        # offset, and the store must still hand back UTC.
        options += f" -c timezone={timezone}"
    separator = "&" if "?" in base else "?"
    dsn = f"{base}{separator}options={quote(options, safe='')}"

    with psycopg.connect(base) as admin:
        admin.execute(f"CREATE SCHEMA IF NOT EXISTS {TEST_SCHEMA}")
    with psycopg.connect(dsn) as connection:
        connection.execute("DROP TABLE IF EXISTS events")
    return dsn

"""Open the event store the settings name, for the example jobs.

The two backends are constructed explicitly (there is no hidden singleton), so a job that wants to run
against either one makes the choice once, here. SQLite is the default; PostgreSQL needs the `postgres`
extra and a DSN.
"""

from __future__ import annotations

from auto_html_report import PostgresEventStore, Settings, SqliteEventStore
from auto_html_report.application.ports import EventStore


def open_store(settings: Settings) -> EventStore:
    """The store selected by ``AUTO_HTML_REPORT_EVENT_STORE``."""
    if settings.event_store == "postgres":
        return PostgresEventStore.from_settings(settings)
    return SqliteEventStore.from_settings(settings)

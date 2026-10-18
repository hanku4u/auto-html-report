"""Concrete event stores: SQLite today, PostgreSQL behind an optional extra in stage 11.

Import the one the job needs. Nothing in here chooses a backend for you -- the job's
``SqliteEventStore.from_settings(settings)`` call is the choice, visible at the call site
(``docs/04-events-and-stores.md`` §4).
"""

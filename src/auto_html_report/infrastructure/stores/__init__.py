"""Concrete event stores: SQLite by default, PostgreSQL behind the optional ``postgres`` extra.

Import the one the job needs. Nothing in here chooses a backend for you -- the job's
``SqliteEventStore.from_settings(settings)`` call is the choice, visible at the call site
(``docs/04-events-and-stores.md`` §4). The PostgreSQL module imports psycopg only when a store is
constructed, so importing this package does not require the extra (ADR-0031).
"""

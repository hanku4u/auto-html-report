"""Predefined report factories, one module per family.

A factory registers itself when its module is imported (``docs/03-report-api.md`` §4); nothing scans this
package, because a job script should say which families it deploys. Importing a family is therefore a
deployment decision, not an implementation detail.
"""

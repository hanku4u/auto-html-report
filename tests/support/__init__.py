"""Test support: builders and fakes shared across the suite.

Importable as ``tests.support.<module>`` because ``pythonpath = ["."]`` is set in
``pyproject.toml``. Deliberately *not* shipped inside ``auto_html_report``: test doubles are not part of
the product, and putting them in the package would make every consumer download them.
"""

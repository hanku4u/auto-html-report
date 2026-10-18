"""The built-in blocks.

Each module in this package defines one block class and registers it with ``@register_block``. The
registry scans this package lazily and recursively -- see
:func:`auto_inform.registry.import_builtin_blocks` -- which has one consequence worth knowing when
adding a block here: **the scan imports every module in this package**, so a module-level import is paid
by every process that looks a block up, including one that never uses that block. Import heavyweight
libraries inside the method that needs them.

The one sanctioned exception is ``numpy`` in ``wafer_map.py``, which is imported at module scope: it is a
declared direct dependency rather than a transitive one, it is an order of magnitude cheaper to import
than matplotlib, and the block's type annotation needs it. matplotlib, by contrast, is imported inside
``render()`` for exactly the reason above.
"""

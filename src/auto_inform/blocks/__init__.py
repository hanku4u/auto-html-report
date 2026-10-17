"""The built-in blocks.

Each module in this package defines one block class and registers it with ``@register_block``. The
registry scans this package lazily -- see :func:`auto_inform.registry.import_builtin_blocks` -- which has
one consequence worth knowing when adding a block here: **the scan imports every module in this
package**, so a module-level import is paid by every process that looks a block up, including one that
never uses that block. Import heavyweight libraries inside the method that needs them.
"""

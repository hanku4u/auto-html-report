"""Figure rendering: a matplotlib Figure to an embeddable PNG data URI.

Three rules from ``docs/06-output-html.md`` §5 are enforced here, each for a reason that only shows up
in production:

* **``matplotlib.use("Agg")`` before ``pyplot`` is imported.** There is no display in CI, and
  importing pyplot first is the classic headless failure.
* **matplotlib is imported lazily**, so a report built only from HTML tables never pays for it.
* **A module-level lock serialises access to matplotlib**, which is not thread-safe.

See also ``docs/02-block-authoring.md`` §3: figures are created in ``render()``, which is pure.
"""

from __future__ import annotations

import base64
import io
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import matplotlib.figure

#: matplotlib mutates process-global state -- the figure manager, font caches, the backend -- so every
#: call into it from this module holds this lock. Charts are usually the slowest part of a report, so
#: the contention is real but small; correctness wins.
_FIGURE_LOCK = threading.Lock()

#: Cached module handle, populated on first use. The annotation is loose on purpose: naming
#: ``matplotlib.pyplot``'s type at import time would defeat the lazy import.
_pyplot: Any = None


def figure_to_data_uri(figure: matplotlib.figure.Figure, *, dpi: int = 100) -> str:
    """Serialise ``figure`` as a PNG and return it as ``data:image/png;base64,...``.

    Thread-safe: the lock is held across the save only, never across a network call, and nothing here
    sleeps.

    **The caller closes the figure.** This function deliberately does not, because the figure is the
    caller's object and ownership transfer would be invisible in a code review. Follow the pattern in
    the spec::

        fig, ax = plt.subplots()
        try:
            ...
            uri = figure_to_data_uri(fig)
        finally:
            plt.close(fig)

    Skipping the ``finally`` leaks a figure per call, so a long-running process accumulates them until
    matplotlib's figure-count warning fills the log.
    """
    pyplot = _load_pyplot()
    buffer = io.BytesIO()
    try:
        with _FIGURE_LOCK:
            # The first call pays matplotlib's backend setup, so do it under the lock: two threads
            # racing that setup is exactly the kind of flake nobody can reproduce.
            pyplot.savefig(buffer, format="png", dpi=dpi)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    finally:
        buffer.close()
    return f"data:image/png;base64,{encoded}"


def _load_pyplot() -> Any:
    """Import and configure pyplot on first use, then reuse the handle."""
    global _pyplot
    if _pyplot is None:
        import matplotlib

        # Must happen before pyplot is imported. force=True is a safety net for the case where
        # something else in the process imported pyplot first: switching the backend late is better
        # than failing at save time in CI.
        matplotlib.use("Agg", force=True)

        from matplotlib import pyplot

        _pyplot = pyplot
    return _pyplot

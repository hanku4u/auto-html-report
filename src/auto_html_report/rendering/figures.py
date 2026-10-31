"""Figure rendering: a matplotlib Figure to an embeddable PNG data URI.

Three rules from ``docs/06-output-html.md`` §5 are enforced here, each for a reason that only shows up
in production:

* **``matplotlib.use("Agg")`` before ``pyplot`` is used.** There is no display in CI, and the classic
  failure is reaching a GUI backend in a headless job.
* **matplotlib is imported lazily**, so a report built only from HTML tables never pays for it.
* **matplotlib's global state is serialised by one re-entrant lock**, because matplotlib is not
  thread-safe -- and that applies to *creating* and *closing* figures just as much as to saving them.

That last point is the one that is easy to get wrong, so it is worth stating precisely: this module can
only serialise the work it performs itself. Saving happens here; **creating** a figure happens in a
block's ``render()``, via ``plt.subplots()``, which this module never sees. Block authors therefore have
to opt into the lock, which is what :func:`figure_lock` is for. See ADR-0025.

See also ``docs/02-block-authoring.md`` §3: figures are created in ``render()``, which is pure.
"""

from __future__ import annotations

import base64
import io
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import matplotlib.figure

#: matplotlib mutates process-global state -- the figure manager and registry, font caches, the active
#: backend -- so every call into it from this module holds this lock. Charts are usually the slowest
#: part of a report, so the contention is real but small; correctness wins.
#:
#: Re-entrant on purpose: the documented block pattern holds this lock while creating, drawing and
#: closing, and calls :func:`figure_to_data_uri` -- which takes the lock again -- from inside that block.
#: A plain ``Lock`` would deadlock there, which is a trap worth one line of configuration to avoid.
_FIGURE_LOCK = threading.RLock()

#: Cached module handle, populated on first use. The annotation is loose on purpose: naming
#: ``matplotlib.pyplot``'s type at import time would defeat the lazy import.
_pyplot: Any = None


@contextmanager
def figure_lock() -> Iterator[None]:
    """Hold the figure lock across matplotlib work, and pin the Agg backend first.

    Block authors wrap **all** of their matplotlib work in this -- creating a figure, drawing on it,
    saving it and closing it -- because matplotlib's global state is not thread-safe and blocks run
    concurrently::

        with figure_lock():
            fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
            try:
                ax.imshow(data, cmap="RdYlGn")
                fig.tight_layout()
                uri = figure_to_data_uri(fig)      # re-entrant: safe inside this block
            finally:
                plt.close(fig)

    The cost is real and accepted: every chart in a report is serialised, so a report of twelve charts
    gets no parallelism for them. That is the price of matplotlib's process-global state, and the
    alternative -- a process per block -- is rejected in ADR-0005.

    Calling this also pins the Agg backend, before the caller creates anything: switching backends
    after a figure exists is the situation ``force=True`` exists to survive, not one to rely on.
    """
    with _FIGURE_LOCK:
        # Inside the lock, deliberately: the first caller does backend setup and publishes the cached
        # module, and two threads racing that is exactly the kind of flake nobody can reproduce.
        _load_pyplot()
        yield


def figure_to_data_uri(figure: matplotlib.figure.Figure, *, dpi: int = 100) -> str:
    """Serialise ``figure`` as a PNG and return it as ``data:image/png;base64,...``.

    Thread-safe: the lock is held across the save only, never across a network call, and nothing here
    sleeps. Note that only *saving* is covered -- creation happens in the caller's code, so wrap it in
    :func:`figure_lock`.

    **The caller closes the figure.** This function deliberately does not, because the figure is the
    caller's object and ownership transfer would be invisible in a code review. Skipping the close
    leaks a figure per call, so a long-running process accumulates them until matplotlib's figure-count
    warning fills the log.
    """
    buffer = io.BytesIO()
    try:
        with figure_lock():
            # ``figure.savefig``, never ``pyplot.savefig``: the pyplot function takes no figure argument
            # and saves whatever it considers current, so it would silently embed the wrong chart
            # whenever more than one figure exists. That was a real defect here (ADR-0025).
            figure.savefig(buffer, format="png", dpi=dpi)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    finally:
        buffer.close()
    return f"data:image/png;base64,{encoded}"


def _load_pyplot() -> Any:
    """Import and configure pyplot on first use, then reuse the handle."""
    global _pyplot
    if _pyplot is None:
        import matplotlib

        # Must happen before pyplot is used. force=True is a safety net for the case where something
        # else in the process already reached pyplot: switching the backend late is better than
        # failing at save time in CI.
        matplotlib.use("Agg", force=True)

        from matplotlib import pyplot

        _pyplot = pyplot
    return _pyplot

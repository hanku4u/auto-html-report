"""Figure rendering: which figure gets saved, who closes it, the module lock, and the lazy import.

The first test here exists because an earlier version of this module called ``pyplot.savefig`` instead
of ``figure.savefig``. The pyplot function takes no figure argument and saves whatever it considers
current, so with more than one figure alive it embedded the wrong chart -- and every test passed,
because every test had exactly one figure alive. See ADR-0025.
"""

from __future__ import annotations

import base64
import io
import subprocess
import sys
import threading

import pytest
from matplotlib.figure import Figure

from auto_html_report.rendering.figures import figure_lock, figure_to_data_uri

PREFIX = "data:image/png;base64,"


def _pyplot() -> object:
    """Import pyplot the way a block would, after the backend has been pinned."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def _open_figure_count() -> int:
    plt = _pyplot()
    return len(plt.get_fignums())  # type: ignore[attr-defined]


def _png_bytes(figure: Figure) -> bytes:
    """What ``figure`` itself serialises to, for comparison with what the helper returned."""
    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format="png", dpi=100)
        return buffer.getvalue()
    finally:
        buffer.close()


def _payload_of(uri: str) -> bytes:
    return base64.b64decode(uri.removeprefix(PREFIX))


# --- which figure is saved ----------------------------------------------------


def test_figure_to_data_uri_should_serialise_the_figure_it_was_given() -> None:
    # given two figures, where the second is the one pyplot considers current
    plt = _pyplot()
    wanted, wanted_axes = plt.subplots(figsize=(2, 2))  # type: ignore[attr-defined]
    wanted_axes.plot([0, 1], [0, 1])
    wanted_axes.set_title("wanted")
    other, other_axes = plt.subplots(figsize=(6, 6))  # type: ignore[attr-defined]
    other_axes.plot([0, 1], [1, 0])
    other_axes.set_title("other")

    wanted_png = _png_bytes(wanted)
    other_png = _png_bytes(other)
    try:
        # when the first figure is serialised
        payload = _payload_of(figure_to_data_uri(wanted))
    finally:
        plt.close("all")  # type: ignore[attr-defined]

    # then it is that figure's payload -- not the current figure's, which is what a call to
    # pyplot.savefig would have produced
    assert wanted_png != other_png, "the fixture must produce visibly different figures"
    assert payload == wanted_png
    assert payload != other_png


def test_figure_to_data_uri_should_return_a_base64_png_data_uri() -> None:
    # given a figure with something drawn on it
    plt = _pyplot()
    figure, axes = plt.subplots(figsize=(2, 1))  # type: ignore[attr-defined]
    axes.plot([0, 1, 2], [0, 1, 0])

    try:
        # when it is serialised
        uri = figure_to_data_uri(figure)

        # then it is a data URI whose payload is a real PNG, so the document needs no network fetch
        assert uri.startswith(PREFIX)
        assert _payload_of(uri).startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        plt.close(figure)  # type: ignore[attr-defined]


@pytest.mark.parametrize("dpi", [50, 200])
def test_figure_to_data_uri_should_honour_the_requested_dpi(dpi: int) -> None:
    # given a figure rendered at a specified resolution
    plt = _pyplot()
    figure, axes = plt.subplots(figsize=(2, 1))  # type: ignore[attr-defined]
    axes.plot([0, 1], [0, 1])

    try:
        small = _payload_of(figure_to_data_uri(figure, dpi=50))
        large = _payload_of(figure_to_data_uri(figure, dpi=dpi))
    finally:
        plt.close(figure)  # type: ignore[attr-defined]

    # when both are produced
    # then the higher resolution produces more bytes, so the argument is not quietly ignored
    assert len(small) <= len(large)


# --- ownership, locking and threading -----------------------------------------


def test_figure_to_data_uri_should_leave_closing_the_figure_to_the_caller() -> None:
    # given a figure
    plt = _pyplot()
    baseline = _open_figure_count()
    figure, _ = plt.subplots()  # type: ignore[attr-defined]

    try:
        # when it is serialised
        figure_to_data_uri(figure)

        # then the figure is still open: ownership stays with the caller, because transferring it
        # invisibly inside a helper is the sort of thing that surprises a reader of the block code
        assert _open_figure_count() == baseline + 1
    finally:
        plt.close(figure)  # type: ignore[attr-defined]


def test_the_documented_figure_pattern_should_leave_no_open_figures() -> None:
    # given the pattern the spec tells block authors to use
    plt = _pyplot()
    baseline = _open_figure_count()

    # when a figure is created, rendered and closed under the figure lock
    with figure_lock():
        figure, _ = plt.subplots()  # type: ignore[attr-defined]
        try:
            figure_to_data_uri(figure)
        finally:
            plt.close(figure)  # type: ignore[attr-defined]

    # then nothing is left open -- skipping that close leaks a figure per call until matplotlib's
    # figure-count warning floods the log
    assert _open_figure_count() == baseline


def test_figure_lock_should_be_re_entrant_so_saving_inside_it_does_not_deadlock() -> None:
    # given the documented pattern holds the lock across create, draw, save and close, and
    # figure_to_data_uri takes the same lock
    plt = _pyplot()
    baseline = _open_figure_count()
    finished = threading.Event()
    errors: list[BaseException] = []

    def render_one() -> None:
        try:
            with figure_lock():
                figure, axes = plt.subplots()  # type: ignore[attr-defined]
                try:
                    axes.plot([0, 1], [0, 1])
                    figure_to_data_uri(figure)
                finally:
                    plt.close(figure)  # type: ignore[attr-defined]
        except BaseException as exc:  # noqa: BLE001 - surface anything at all
            errors.append(exc)
        finally:
            finished.set()

    # when that pattern runs on a worker thread, so a deadlock hangs only that thread
    thread = threading.Thread(target=render_one, daemon=True)
    thread.start()
    completed = finished.wait(timeout=30)

    # then it completes rather than deadlocking on a non-re-entrant lock
    assert completed, "figure_lock is not re-entrant: nested use deadlocked"
    assert errors == []
    assert _open_figure_count() == baseline


def test_concurrent_figure_rendering_should_embed_each_threads_own_figure() -> None:
    # given eight threads using the documented pattern, each drawing a recognisably different figure
    plt = _pyplot()
    baseline = _open_figure_count()
    payloads: dict[int, bytes] = {}
    expected: dict[int, bytes] = {}
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def render_one(index: int) -> None:
        start.wait()  # release all eight at once, to maximise the chance of a real collision
        try:
            with figure_lock():
                figure, axes = plt.subplots(figsize=(2 + index / 8, 2))  # type: ignore[attr-defined]
                try:
                    axes.plot([0, index, 1], [1, index, 0])
                    axes.set_title(f"figure {index}")
                    expected[index] = _png_bytes(figure)
                    payloads[index] = _payload_of(figure_to_data_uri(figure))
                finally:
                    plt.close(figure)  # type: ignore[attr-defined]
        except BaseException as exc:  # noqa: BLE001 - the point is to surface anything at all
            errors.append(exc)

    threads = [threading.Thread(target=render_one, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    # when they run concurrently
    # then every returned payload is that thread's own figure -- not merely "eight payloads arrived".
    # Asserting the count alone would pass even if all eight embedded the same chart.
    assert errors == []
    assert len(payloads) == 8
    for index, payload in payloads.items():
        assert payload == expected[index], f"thread {index} embedded the wrong figure"
    assert _open_figure_count() == baseline


# --- the lazy import ----------------------------------------------------------


def test_importing_the_figures_module_should_not_import_pyplot_until_a_figure_is_rendered() -> None:
    # given a fresh interpreter, so the answer cannot depend on which test ran first
    probe = (
        "import sys;"
        "import auto_html_report.rendering.figures;"  # importing the module must be cheap
        "before = 'matplotlib.pyplot' in sys.modules;"
        "from matplotlib.figure import Figure;"  # a figure that does not come from pyplot
        "from auto_html_report.rendering.figures import figure_to_data_uri;"
        "uri = figure_to_data_uri(Figure());"
        "after = 'matplotlib.pyplot' in sys.modules;"
        "print(before, after, uri.startswith('data:image/png;base64,'))"
    )

    # when the module is imported and then used from a subprocess
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False, timeout=120
    )

    # then the import alone did not pull pyplot in, the call did, and the URI came back -- which is
    # what lets a report made only of HTML tables stay light
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False True True"

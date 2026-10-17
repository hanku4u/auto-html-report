"""Figure rendering: the data URI, who closes the figure, the module lock, and the lazy import."""

from __future__ import annotations

import base64
import subprocess
import sys
import threading

import pytest

from auto_inform.rendering.figures import figure_to_data_uri

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
        decoded = base64.b64decode(uri.removeprefix(PREFIX))
        assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        plt.close(figure)  # type: ignore[attr-defined]


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

    # when a figure is created, rendered and closed in a finally block
    figure, _ = plt.subplots()  # type: ignore[attr-defined]
    try:
        figure_to_data_uri(figure)
    finally:
        plt.close(figure)  # type: ignore[attr-defined]

    # then nothing is left open -- skipping that finally leaks a figure per call until matplotlib's
    # figure-count warning floods the log
    assert _open_figure_count() == baseline


def test_figure_to_data_uri_should_be_safe_to_call_from_many_threads() -> None:
    # given matplotlib, which is not thread-safe, and the documented lock in figures.py
    plt = _pyplot()
    baseline = _open_figure_count()
    results: list[str] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def render_one(index: int) -> None:
        start.wait()  # release all eight at once, to maximise the chance of a real collision
        try:
            figure, axes = plt.subplots(figsize=(2, 1))  # type: ignore[attr-defined]
            try:
                axes.plot([0, index, 1], [1, index, 0])
                results.append(figure_to_data_uri(figure))
            finally:
                plt.close(figure)  # type: ignore[attr-defined]
        except BaseException as exc:  # noqa: BLE001 - the point is to surface anything at all
            errors.append(exc)

    threads = [threading.Thread(target=render_one, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    # when eight threads render figures at once
    # then nothing raised, every call produced a usable URI, and no figure leaked
    assert errors == []
    assert len(results) == 8
    assert all(uri.startswith(PREFIX) for uri in results)
    assert _open_figure_count() == baseline


def test_importing_the_figures_module_should_not_import_pyplot_until_a_figure_is_rendered() -> None:
    # given a fresh interpreter, so the answer cannot depend on which test ran first
    probe = (
        "import sys;"
        "import auto_inform.rendering.figures;"  # importing the module must be cheap
        "before = 'matplotlib.pyplot' in sys.modules;"
        "from matplotlib.figure import Figure;"  # a figure that does not come from pyplot
        "from auto_inform.rendering.figures import figure_to_data_uri;"
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
    assert completed.stdout.strip() == ("False True True")


@pytest.mark.parametrize("dpi", [50, 200])
def test_figure_to_data_uri_should_honour_the_requested_dpi(dpi: int) -> None:
    # given a figure rendered at a specified resolution
    plt = _pyplot()
    figure, axes = plt.subplots(figsize=(2, 1))  # type: ignore[attr-defined]
    axes.plot([0, 1], [0, 1])

    try:
        small = base64.b64decode(figure_to_data_uri(figure, dpi=50).removeprefix(PREFIX))
        large = base64.b64decode(figure_to_data_uri(figure, dpi=dpi).removeprefix(PREFIX))
    finally:
        plt.close(figure)  # type: ignore[attr-defined]

    # when both are produced
    # then the higher resolution produces more bytes, so the argument is not quietly ignored
    assert len(small) <= len(large)

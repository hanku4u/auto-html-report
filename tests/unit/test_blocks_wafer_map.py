"""The wafer-map block, exercised against a fake client.

The chart itself is a smoke test -- never assert on pixels -- but three things here are worth pinning:
the collect path and its timeout, the escaping of the lot id in the ``alt`` attribute, and that no figure
is left open. The last of those is the leak that matplotlib's figure-count warning eventually complains
about in a long-running process.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

from auto_inform.blocks.wafer_map import WaferMapBlock
from auto_inform.infrastructure.settings import Settings
from auto_inform.registry import get_block
from tests.support.contexts import make_render_context, make_report_context
from tests.support.http import FakeHttpClient

PATH = "/wafers/LOT-88213/binmap"

FIXTURE = {"bins": [[0.0, 1.0, 0.0], [1.0, 0.0, 1.0], [0.0, 0.0, 1.0]]}

PREFIX = "data:image/png;base64,"


def _http(payload: object = FIXTURE) -> FakeHttpClient:
    return FakeHttpClient({PATH: payload})


def _pyplot() -> object:
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot

    return pyplot


def _open_figure_count() -> int:
    pyplot = _pyplot()
    return len(pyplot.get_fignums())  # type: ignore[attr-defined]


# --- collect ------------------------------------------------------------------


def test_collect_should_return_the_bin_map_as_a_float_array() -> None:
    # given a lot with a bin map at the API
    context = make_report_context(http=_http())

    # when the block collects
    # then it returns an array of the documented shape, typed for the renderer
    data = WaferMapBlock(lot_id="LOT-88213").collect(context)

    assert isinstance(data, np.ndarray)
    assert data.shape == (3, 3)
    assert data.dtype == float
    # .tolist() rather than a bare ==: comparing arrays element-wise yields an array of booleans, and
    # asserting on that raises "truth value is ambiguous" instead of testing anything
    assert data[0].tolist() == [0.0, 1.0, 0.0]


def test_collect_should_ask_for_the_lots_bin_map() -> None:
    # given the API path the block documents
    http = _http()

    # when it collects
    WaferMapBlock(lot_id="LOT-88213").collect(make_report_context(http=http))

    # then it asked that lot's bin map, and nothing else
    assert http.paths == (PATH,)


def test_collect_should_take_its_timeout_from_settings() -> None:
    # given a configured block timeout
    http = _http()

    # when a block collects
    WaferMapBlock(lot_id="LOT-88213").collect(
        make_report_context(http=http, settings=Settings(block_timeout_seconds=9.0))
    )

    # then the call carried it, so the block cannot outlive its budget
    assert http.calls[0].timeout == 9.0


# --- render -------------------------------------------------------------------


def test_render_should_embed_the_chart_as_a_data_uri() -> None:
    # given a bin map
    data = np.asarray(FIXTURE["bins"], dtype=float)

    # when the block renders
    html = WaferMapBlock(lot_id="LOT-88213").render(data, make_render_context())

    # then the image travels with the document rather than being fetched, and it is marked as a figure
    assert html.startswith('<img class="ai-figure" src="data:image/png;base64,')
    assert PREFIX in html
    assert 'alt="Wafer bin map for LOT-88213"' in html


def test_render_should_escape_the_lot_id_in_the_alt_attribute() -> None:
    # given a lot id that arrived from a payload and contains quotes and markup -- an f-string inside an
    # attribute is exactly where escaping gets forgotten
    hostile = '" onload="alert(1)'

    # when the block renders
    html = WaferMapBlock(lot_id=hostile).render(np.zeros((2, 2)), make_render_context())

    # then it cannot break out of the alt attribute
    assert 'onload="alert(1)"' not in html
    assert "&quot;" in html or "&#34;" in html


def test_render_should_leave_no_open_figures() -> None:
    # given the figure pattern the block is written to
    baseline = _open_figure_count()

    # when it renders
    WaferMapBlock(lot_id="LOT-88213").render(np.zeros((3, 3)), make_render_context())

    # then the figure was closed. Skipping that leaks one figure per report row until matplotlib's
    # figure-count warning floods the log of a long-running process
    assert _open_figure_count() == baseline


# --- the block's identity and registration ------------------------------------


def test_the_block_should_be_registered_under_its_display_name() -> None:
    # given the built-in block package
    # when the registry is asked for this block by name
    # then the scan found it, so a predefined report can name it
    assert get_block("Wafer map") is WaferMapBlock


def test_name_should_combine_the_display_name_with_the_lot() -> None:
    # given a block for one lot
    # then its Item text identifies the lot, so several lots in one report stay distinguishable
    assert WaferMapBlock(lot_id="LOT-88213").name == "Wafer map — LOT-88213"


# --- the lazy import ----------------------------------------------------------


def test_scanning_the_built_in_blocks_should_not_import_matplotlib() -> None:
    # given a fresh interpreter, so the answer cannot depend on which test ran first
    probe = (
        "import sys;"
        "import auto_inform;"
        "from auto_inform.registry import available_blocks;"
        "available_blocks();"
        "print('matplotlib' in sys.modules, 'matplotlib.pyplot' in sys.modules)"
    )

    # when the registry scans every block module
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False, timeout=120
    )

    # then matplotlib was not imported. The scan imports every module in blocks/, so a module-level
    # pyplot import in any of them would make every report -- including one of HTML tables only -- pay
    # for matplotlib, which is the cost the lazy import exists to avoid (docs/06-output-html.md §5)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False False"


@pytest.mark.parametrize("shape", [(1, 1), (25, 25)])
def test_render_should_cope_with_the_smallest_and_the_typical_bin_map(
    shape: tuple[int, int],
) -> None:
    # given bin maps at both ends of the plausible range
    # when they are rendered
    html = WaferMapBlock(lot_id="LOT-1").render(np.zeros(shape), make_render_context())

    # then both produce an embedded image, so nothing about the chart depends on a minimum size
    assert html.startswith('<img class="ai-figure" src="data:image/png;base64,')

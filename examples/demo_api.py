"""Canned API responses, so the examples run without a network.

These stand in for the yield and maintenance APIs. ``demo_http()`` answers them from a stub client (``docs/07``
§2), and ``examples.local_api`` serves the same data over real HTTP. With a real client the wiring changes and
the scripts do not -- they are written against the ``HttpClient`` port, not against the stub.
"""

from __future__ import annotations

from typing import Any

from auto_html_report.testing import StubHttpClient

#: The lots the demo API reports as failed, and the tool they ran on.
LOT_IDS = ("L1001", "L1002")
TOOL_ID = "T-104"

#: A 6x6 bin map per lot. Not physically meaningful; the point is that a chart is rendered from it.
_BIN_MAP = {
    "bins": [
        [1.0, 1.0, 0.0, 0.0, 1.0, 1.0],
        [1.0, 0.0, 0.0, 0.0, 1.0, 1.0],
        [1.0, 1.0, 0.0, 1.0, 1.0, 1.0],
        [1.0, 1.0, 0.0, 1.0, 0.0, 1.0],
        [1.0, 0.0, 0.0, 0.0, 1.0, 1.0],
        [1.0, 1.0, 0.0, 1.0, 1.0, 1.0],
    ]
}

_MAINTENANCE = {
    "records": [
        {
            "performed_at": "2026-10-12T08:30:00+00:00",
            "engineer": "A. Rivera",
            "summary": "Replaced the focus ring after a drift alarm.",
        },
        {
            "performed_at": "2026-10-05T14:10:00+00:00",
            "engineer": "S. Okafor",
            "summary": "Scheduled chamber clean.",
        },
    ]
}


#: Every path the example jobs use, and its response.
ROUTES: dict[str, Any] = {
    "/yield/failures": {"lots": list(LOT_IDS)},
    **{f"/wafers/{lot_id}/binmap": _BIN_MAP for lot_id in LOT_IDS},
    f"/maintenance/{TOOL_ID}": _MAINTENANCE,
}


def demo_http() -> StubHttpClient:
    """A stub client answering the paths the example jobs use."""
    return StubHttpClient(ROUTES)

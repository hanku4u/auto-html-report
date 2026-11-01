"""The data ``examples.local_api`` serves, standing in for yield and maintenance APIs."""

from __future__ import annotations

from typing import Any

LOT_IDS = ("L1001", "L1002")
TOOL_ID = "T-104"

#: A 6x6 bin map. Not physically meaningful; the point is that a chart is rendered from it.
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

ROUTES: dict[str, Any] = {
    "/yield/failures": {"lots": list(LOT_IDS)},
    **{f"/wafers/{lot_id}/binmap": _BIN_MAP for lot_id in LOT_IDS},
    f"/maintenance/{TOOL_ID}": _MAINTENANCE,
}

"""The filename rule of ``docs/06-output-html.md`` §2.

``<slug(title)>__[<slug(event_id)>__]<digest>__<timestamp>.html``, with the event segment only when the
definition names an event. The property that matters is not the exact string but that the name is safe for
a filesystem and an S3 key, that two *different* events never collide, and that regenerating the same
report in the same second is idempotent.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from auto_inform._naming import DEFAULT_MAX_LENGTH
from auto_inform.generate import report_filename
from tests.support.builders import make_report, make_row


def _name(*, title: str = "LINE 4 — Daily Health", event_id: str | None = None) -> str:
    report = make_report(make_row("Row"), title=title, event_id=event_id)
    return report_filename(report.definition, report)


def test_report_filename_should_use_the_slugged_title_and_the_utc_timestamp() -> None:
    # given a report with no event
    # when its filename is derived
    # then it is the slugged title and the fixed generation time, in the documented format
    assert _name() == "line-4-daily-health__0ca5c39b7027bf27__20261017T090000Z.html"


def test_report_filename_with_an_event_should_add_the_middle_segment() -> None:
    # given a report built for a discovered event
    # when its filename is derived
    # then the event identity appears between the title and the timestamp, which is what makes two
    # reports of the same shape addressable separately
    assert _name(event_id="WAFER_FAILURE:LOT-88213") == (
        "line-4-daily-health__wafer-failure-lot-88213__5cc6b8c8398ec16b__20261017T090000Z.html"
    )


def test_report_filename_should_not_contain_a_path_separator() -> None:
    # given a title containing a directory separator, which would otherwise escape the output directory
    name = _name(title="../../etc/passwd")

    # when the filename is derived
    # then no separator survives
    assert "/" not in name
    assert ".." not in name


def test_report_filename_with_an_emoji_only_title_should_fall_back_to_report() -> None:
    # given a title with nothing sluggable in it
    # when the filename is derived
    # then a fallback keeps the name usable rather than producing an empty segment
    assert _name(title="🎉🎉").startswith("report__")


def test_report_filename_should_cap_the_title_segment() -> None:
    # given a 300-character title
    name = _name(title="x" * 300)

    # when the filename is derived
    # then the title segment respects the documented cap, so the whole name stays within filesystem and
    # S3 key limits
    assert len(name.split("__")[0]) <= DEFAULT_MAX_LENGTH


def test_report_filename_with_a_naive_generated_at_should_raise_value_error() -> None:
    # given a report whose timestamp lost its timezone
    report = make_report(make_row("Row"), generated_at=datetime(2026, 10, 17, 9, 0))

    # when the filename is derived
    # then it is refused rather than named as if generated at the host's local offset
    with pytest.raises(ValueError, match="timezone-aware"):
        report_filename(report.definition, report)


def test_report_filename_should_be_deterministic() -> None:
    # given the same report twice
    report = make_report(make_row("Row"), event_id="EVT-1")

    # when the filename is derived twice
    # then it is identical, which is what allows the name to be computed before writing and compared after
    assert report_filename(report.definition, report) == report_filename(report.definition, report)


def test_report_filename_is_unique_only_to_the_second() -> None:
    # given two reports with the same title and event, generated at the same instant
    first = make_report(make_row("Row"), title="Same", event_id="SAME-EVENT")
    second = make_report(make_row("Row"), title="Same", event_id="SAME-EVENT")

    # then the names collide. This is a known property rather than a surprise: uniqueness holds per
    # title, event and *second*, and the scheduled path is protected by distinct event ids plus the store
    # marking work processed, not by the clock (docs/06-output-html.md §2)
    assert report_filename(first.definition, first) == report_filename(second.definition, second)


def test_two_distinct_events_that_slug_identically_should_get_different_names() -> None:
    # given two event ids that the slugger folds together -- "A" and "a", which is exactly the collision
    # a shared S3 bucket makes irreversible (one report silently replaces the other)
    upper = _name(event_id="LOT-A")
    lower = _name(event_id="lot-a")

    # when their names are compared
    # then the identity digest keeps them apart even though the slugged segments agree
    assert upper.split("__")[0] == lower.split("__")[0]
    assert upper.split("__")[1] == lower.split("__")[1]  # the slug is the same...
    assert upper != lower  # ...but the digest is not

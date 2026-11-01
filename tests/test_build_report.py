"""``build_report``: the context check, one call per distinct request, retries, and failure handling."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from auto_html_report import (
    Block,
    FetchError,
    MissingContextError,
    ReportError,
    TransientError,
    build_report,
    escape,
)
from auto_html_report.report import _Call, _run_with_retries

NO_DELAY: dict[str, Any] = {"retry_delay": 0.0}


class Calls:
    """A gather function that counts its calls, and fails the first ``failures`` of them."""

    def __init__(self, failures: int = 0, error: Exception | None = None) -> None:
        self.arguments: list[str] = []
        self.failures = failures
        self.error = error or TransientError("try again")
        self.lock = threading.Lock()

    def __call__(self, tool_id: str) -> dict[str, str]:
        with self.lock:
            self.arguments.append(tool_id)
            attempt = len(self.arguments)
        if attempt <= self.failures:
            raise self.error
        return {"tool": tool_id}


def block_class(source: Any, title: str = "Row") -> type[Block]:
    class Shown(Block):
        gather = source

        def render(self, data: Any) -> str:
            return f"<b>{escape(str(data))}</b>"

    Shown.title = title
    return Shown


def test_blocks_sharing_a_function_and_arguments_should_make_one_call() -> None:
    # given three blocks that gather through the same function
    calls = Calls()
    blocks = [block_class(calls, title)() for title in ("One", "Two", "Three")]

    # when the report is built
    report = build_report("T", blocks, {"tool_id": "T-1"})

    # then there was one call, and every block received its result
    assert calls.arguments == ["T-1"]
    assert all("T-1" in row.html for row in report.rows)


def test_a_constructor_argument_should_override_the_context_and_be_its_own_call() -> None:
    # given the same block for two tools, one using the context and one overriding it
    calls = Calls()
    shown = block_class(calls)
    blocks = [shown(title="Context"), shown(title="Other", tool_id="T-2")]

    # when the report is built
    report = build_report("T", blocks, {"tool_id": "T-1"})

    # then each tool was fetched once, and each row shows its own
    assert sorted(calls.arguments) == ["T-1", "T-2"]
    assert "T-1" in report.rows[0].html
    assert "T-2" in report.rows[1].html


def test_equal_but_unhashable_arguments_should_still_make_one_call() -> None:
    # given two blocks whose overrides are equal lists, which can't be dict keys
    seen: list[list[str]] = []

    def gather(lots: list[str]) -> int:
        seen.append(lots)
        return len(lots)

    shown = block_class(gather)

    # when the report is built
    build_report("T", [shown(title="A", lots=["x"]), shown(title="B", lots=["x"])])

    # then they share the call
    assert seen == [["x"]]


def test_arguments_that_cannot_be_compared_should_make_separate_calls() -> None:
    # given override values whose == refuses to give a plain answer, as numpy arrays do
    class Ambiguous:
        def __eq__(self, other: object) -> bool:
            raise ValueError("the truth value is ambiguous")

        __hash__ = None  # type: ignore[assignment]

    seen: list[Ambiguous] = []

    def gather(values: Ambiguous) -> int:
        seen.append(values)
        return 1

    shown = block_class(gather)

    # when the report is built
    report = build_report(
        "T", [shown(title="A", values=Ambiguous()), shown(title="B", values=Ambiguous())]
    )

    # then the report still builds, at the cost of one extra call
    assert len(seen) == 2
    assert report.failed_rows == ()


def test_a_block_with_several_sources_should_receive_them_by_key_and_still_share() -> None:
    # given a block with two sources, one of them shared with another block
    calls = Calls()

    def other() -> str:
        return "other"

    class Both(Block):
        title = "Both"
        gather: ClassVar[dict[str, Any]] = {"tool": calls, "other": other}

        def render(self, data: dict[str, Any]) -> str:
            return f"{data['tool']['tool']} {data['other']}"

    # when the report is built
    report = build_report("T", [Both(), block_class(calls)()], {"tool_id": "T-1"})

    # then it got both results, and the shared one ran once
    assert report.rows[0].html == "T-1 other"
    assert calls.arguments == ["T-1"]


def test_a_block_with_no_gather_should_render_with_none() -> None:
    # given a block that fetches nothing
    class Static(Block):
        title = "Static"

        def render(self, data: None) -> str:
            return f"data is {data}"

    # when the report is built
    # then it renders without any context
    assert build_report("T", [Static()]).rows[0].html == "data is None"


def test_missing_context_keys_should_all_be_reported_before_anything_is_fetched() -> None:
    # given two blocks missing different keys, and a third that could have run
    calls = Calls()

    def needs_http(http: object) -> None:
        raise AssertionError("must not run")

    blocks = [
        block_class(calls, "Fine")(),
        block_class(needs_http, "Needs http")(),
        block_class(lambda tool_id, lot_id: None, "Needs lot")(),
    ]

    # when the report is built
    with pytest.raises(MissingContextError) as caught:
        build_report("T", blocks, {"tool_id": "T-1"})

    # then every gap is named at once, and no fetching started
    message = str(caught.value)
    assert "'Needs http' needs http" in message
    assert "'Needs lot' needs lot_id" in message
    assert calls.arguments == []


def test_a_missing_key_message_should_never_contain_context_values() -> None:
    # given a context holding a secret
    # when another key is missing
    with pytest.raises(MissingContextError) as caught:
        build_report("T", [block_class(lambda http: None)()], {"token": "s3cret"})

    # then the secret isn't in the message
    assert "s3cret" not in str(caught.value)


def test_a_parameter_with_a_default_should_not_be_required() -> None:
    # given a function with a default, and no such key in the context
    def gather(days: int = 30) -> int:
        return days

    # when the report is built
    # then the default applies
    assert build_report("T", [block_class(gather)()]).rows[0].html == "<b>30</b>"


def test_a_misspelled_constructor_argument_should_be_refused() -> None:
    # given a block whose function takes tool_id
    # when it is constructed with a typo, which would otherwise silently use the context's value
    # then it is refused
    with pytest.raises(TypeError, match="tool_idd"):
        block_class(Calls())(tool_idd="T-1")


@pytest.mark.parametrize(
    "error", [TransientError("503"), TimeoutError("slow"), ConnectionError("refused")]
)
def test_a_retryable_failure_should_be_retried_until_it_succeeds(error: Exception) -> None:
    # given a call that fails three times, then works
    calls = Calls(failures=3, error=error)

    # when the report is built with the default three retries
    report = build_report("T", [block_class(calls)()], {"tool_id": "T-1"}, **NO_DELAY)

    # then the fourth attempt succeeded
    assert len(calls.arguments) == 4
    assert report.failed_rows == ()


def test_retries_should_back_off_progressively() -> None:
    # given a stop signal that records how long each retry waits, rather than waiting
    class RecordingStop(threading.Event):
        delays: ClassVar[list[float]] = []

        def wait(self, timeout: float | None = None) -> bool:
            assert timeout is not None
            self.delays.append(timeout)
            return False

    calls = Calls(failures=3)

    # when a call fails three times
    _run_with_retries(_Call(calls, {"tool_id": "T-1"}), 3, 1.0, (TransientError,), RecordingStop())

    # then each delay doubles
    assert RecordingStop.delays == [1.0, 2.0, 4.0]


def test_exhausted_retries_should_fail_the_report_and_keep_the_cause() -> None:
    # given a call that never recovers
    error = TransientError("still down")
    calls = Calls(failures=100, error=error)

    # when the report is built
    with pytest.raises(FetchError) as caught:
        build_report("T", [block_class(calls, "Row")()], {"tool_id": "T-1"}, **NO_DELAY)

    # then it tried four times, and the error names what failed and who needed it
    assert len(calls.arguments) == 4
    assert "4 attempts" in str(caught.value)
    assert "'Row'" in str(caught.value)
    assert "still down" in str(caught.value)
    assert caught.value.__cause__ is error
    assert isinstance(caught.value, ReportError)


def test_a_permanent_failure_should_not_be_retried() -> None:
    # given a call failing with something retrying can't fix
    calls = Calls(failures=100, error=KeyError("records"))

    # when the report is built
    with pytest.raises(FetchError, match="1 attempt"):
        build_report("T", [block_class(calls)()], {"tool_id": "T-1"}, **NO_DELAY)

    # then it was tried once
    assert len(calls.arguments) == 1


def test_retry_on_should_replace_what_counts_as_retryable() -> None:
    # given a library-specific error, and a build that says it is retryable
    class ClientError(Exception):
        pass

    calls = Calls(failures=1, error=ClientError("flaky"))

    # when the report is built
    report = build_report(
        "T", [block_class(calls)()], {"tool_id": "T-1"}, retry_on=(ClientError,), **NO_DELAY
    )

    # then it was retried
    assert len(calls.arguments) == 2
    assert report.failed_rows == ()


def test_one_failed_call_should_stop_the_report_without_waiting_for_the_others() -> None:
    # given one call that fails, and another that blocks until released
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    def blocked() -> None:
        started.set()
        release.wait(30)
        finished.set()

    def failing() -> None:
        started.wait(30)
        raise RuntimeError("no")

    blocks = [block_class(blocked, "A")(), block_class(failing, "B")()]

    # when the report is built
    try:
        with pytest.raises(FetchError, match="no"):
            build_report("T", blocks)
        # then the failure arrived while the other call was still running
        assert not finished.is_set()
    finally:
        release.set()


def test_a_failure_should_end_retries_elsewhere_rather_than_keep_calling() -> None:
    # given one call retrying with a very long delay, and another that fails once the first has tried
    retried = threading.Event()
    attempts: list[int] = []

    def retrying() -> None:
        attempts.append(1)
        retried.set()
        raise TransientError("try again")

    def failing() -> None:
        retried.wait(30)
        raise RuntimeError("no")

    blocks = [block_class(retrying, "A")(), block_class(failing, "B")()]

    # when the report is built
    with pytest.raises(FetchError, match="no"):
        build_report("T", blocks, retry_delay=30.0)

    # then the waiting call gave up rather than retrying 30 seconds later
    assert attempts == [1]


def test_a_hung_call_should_stop_the_report_at_the_fetch_timeout() -> None:
    # given a call that doesn't return in time
    release = threading.Event()

    def hung() -> None:
        release.wait(30)

    # when the report is built with a very short limit
    try:
        with pytest.raises(FetchError, match="longer than"):
            build_report("T", [block_class(hung)()], fetch_timeout=0.001)
    finally:
        release.set()


def test_a_render_failure_should_become_an_error_row_and_keep_the_rest() -> None:
    # given one block whose render raises, with markup in the message, between two that work
    def data() -> str:
        return "ok"

    class Broken(Block):
        title = "Broken"
        gather = data

        def render(self, data: str) -> str:
            raise ValueError("bad <value>")

    blocks = [block_class(data, "First")(), Broken(), block_class(data, "Last")()]

    # when the report is built
    report = build_report("T", blocks)

    # then only that row failed, its message is escaped, and order is kept
    assert [row.title for row in report.rows] == ["First", "Broken", "Last"]
    assert [row.title for row in report.failed_rows] == ["Broken"]
    assert report.rows[1].error == "ValueError: bad <value>"
    assert "bad &lt;value&gt;" in report.html
    assert "bad <value>" not in report.html
    assert "ai-cell--error" in report.html


def test_a_render_returning_something_other_than_text_should_become_an_error_row() -> None:
    # given a render that forgot to return its HTML
    class Forgetful(Block):
        title = "Forgetful"

        def render(self, data: None) -> str:
            return None  # type: ignore[return-value]

    # when the report is built
    # then that's a visible failure, not a cell saying "None"
    assert build_report("T", [Forgetful()]).failed_rows[0].title == "Forgetful"


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: build_report("", [block_class(lambda: 1)()]), "title"),
        (lambda: build_report("T", []), "no blocks"),
        (lambda: build_report("T", [block_class(lambda: 1)()], retries=-1), "retries"),
        (
            lambda: build_report("T", [block_class(lambda: 1)(), block_class(lambda: 2)()]),
            "repeated",
        ),
    ],
)
def test_an_invalid_report_should_be_refused(build: Callable[[], Any], message: str) -> None:
    # given a report that can't be right
    # when it is built
    # then it is refused
    with pytest.raises(ValueError, match=message):
        build()


def test_a_block_without_a_title_should_be_refused() -> None:
    # given a block class that never set a title
    # when it is constructed
    # then it says so
    with pytest.raises(TypeError, match="needs a title"):
        block_class(lambda: 1, "")()


def test_a_gather_method_taking_self_should_be_refused_with_a_clear_reason() -> None:
    # given a block written with gather as a method, the likeliest first mistake
    class AsMethod(Block):
        title = "As method"

        def gather(self, tool_id: str) -> str:
            return tool_id

        def render(self, data: str) -> str:
            return data

    # when it is constructed
    # then it says what to do, rather than later reporting that the context lacks "self"
    with pytest.raises(TypeError, match="plain function"):
        AsMethod()


def test_a_gather_that_is_not_a_function_should_be_refused() -> None:
    # given a block whose gather is the result of a call rather than the function
    # when it is constructed
    # then it is refused
    with pytest.raises(TypeError, match="must be a function"):
        block_class("not a function")()


def test_a_block_with_its_own_constructor_should_work() -> None:
    # given a custom block that derives its title and keeps a setting of its own
    calls = Calls()

    class Limited(Block):
        gather = calls

        def __init__(self, tool_id: str, limit: int) -> None:
            super().__init__(title=f"Tool {tool_id}", tool_id=tool_id)
            self.limit = limit

        def render(self, data: dict[str, str]) -> str:
            return f"{data['tool']} limit {self.limit}"

    # when it goes in a report with no context
    report = build_report("T", [Limited("T-9", 5)])

    # then the title, argument and setting all arrive
    assert report.rows[0].title == "Tool T-9"
    assert report.rows[0].html == "T-9 limit 5"


def test_blocks_should_be_accepted_from_any_iterable() -> None:
    # given a generator of blocks, as a dynamic list might be built
    blocks = (block_class(lambda: 1, name)() for name in ("A", "B"))

    # when the report is built
    # then both are there
    assert len(build_report("T", blocks).rows) == 2


def test_save_should_write_the_document_with_owner_only_access(tmp_path: Path) -> None:
    # given a report
    report = build_report("Tool: Review!", [block_class(lambda: 1)()])

    # when it is saved into a directory that doesn't exist yet
    path = report.save(tmp_path / "new")

    # then the file holds the document under a safe name, private, with no temporary left behind
    assert path.read_text(encoding="utf-8") == report.html
    assert path.name.startswith("tool-review__")
    assert path.stat().st_mode & 0o077 == 0
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_title_subtitle_and_metadata_should_be_escaped_in_the_header() -> None:
    # given framing text containing markup
    report = build_report(
        "<T>",
        [block_class(lambda: 1)()],
        subtitle="<S>",
        metadata={"<K>": "<V>"},
    )

    # when the document is built
    # then none of it became markup, and the three report columns are there
    for text in ("&lt;T&gt;", "&lt;S&gt;", "&lt;K&gt;", "&lt;V&gt;"):
        assert text in report.html
    assert "<T>" not in report.html
    assert all(name in report.html for name in ("Item", "Data", "Check"))

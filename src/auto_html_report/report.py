"""``build_report``: check the context, fetch each distinct call once, render every block."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_html_report.block import Block, named_parameters
from auto_html_report.errors import FetchError, MissingContextError, TransientError
from auto_html_report.rendering import error_cell, render_document

logger = logging.getLogger("auto_html_report")


@dataclass(frozen=True)
class Row:
    """One rendered block. ``error`` is set when its ``render`` raised."""

    title: str
    html: str
    error: str | None = None


@dataclass(frozen=True)
class Report:
    """A finished report: the document, and how each row turned out."""

    title: str
    html: str
    rows: tuple[Row, ...]
    generated_at: datetime

    @property
    def failed_rows(self) -> tuple[Row, ...]:
        return tuple(row for row in self.rows if row.error is not None)

    def save(self, directory: Path | str = ".") -> Path:
        """Write the document into ``directory``, and return its path.

        The file appears all at once, so nobody can open half a report, and it is readable only by its owner
        because reports often hold internal data. Use ``chmod`` on the result to share it more widely.
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:60] or "report"
        target = directory / f"{slug}__{self.generated_at:%Y%m%dT%H%M%S%f}Z.html"

        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".report-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(self.html)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target


@dataclass
class _Call:
    """One distinct function call, and the blocks waiting for its result."""

    function: Callable[..., Any]
    arguments: dict[str, Any]
    needed_by: list[str] = field(default_factory=list)
    result: Any = None


def build_report(
    title: str,
    blocks: Iterable[Block],
    context: Mapping[str, Any] | None = None,
    *,
    subtitle: str | None = None,
    metadata: Mapping[str, str] | None = None,
    retries: int = 3,
    retry_delay: float = 1.0,
    retry_on: tuple[type[Exception], ...] = (TransientError, TimeoutError, ConnectionError),
    max_workers: int = 8,
    fetch_timeout: float = 300.0,
) -> Report:
    """Build a report from ``blocks``, in the order given.

    ``context`` holds whatever the gather functions take as parameters. Identical calls run once, even when
    several blocks need them.

    This fails fast: a missing context key raises :class:`MissingContextError` before anything is fetched, and
    a call that fails for good raises :class:`FetchError`, so no report is produced. Only a failure inside
    ``render`` is contained, as an error in that block's row.

    A call raising one of ``retry_on`` is retried up to ``retries`` times, waiting ``retry_delay`` seconds and
    doubling each time: 1, 2 and 4 seconds by default. Anything else fails immediately.

    Python cannot interrupt a running thread, so gather functions must set their own timeouts on network calls.
    ``fetch_timeout`` stops this function waiting for them, but a call that truly hangs keeps running, and can
    delay the interpreter's exit.
    """
    blocks = list(blocks)
    _check_definition(title, blocks, retries)

    calls, block_calls = _plan(blocks, context or {})
    _fetch(calls, retries, retry_delay, retry_on, max_workers, fetch_timeout)

    rows = tuple(
        _render(block, sources) for block, sources in zip(blocks, block_calls, strict=True)
    )
    generated_at = datetime.now(UTC)
    html = render_document(
        title=title,
        subtitle=subtitle,
        metadata=metadata or {},
        rows=rows,
        generated_at=generated_at,
    )
    return Report(title=title, html=html, rows=rows, generated_at=generated_at)


def _check_definition(title: str, blocks: list[Block], retries: int) -> None:
    if not title:
        raise ValueError("a report needs a title")
    if not blocks:
        raise ValueError(f"report {title!r} has no blocks")
    if retries < 0:
        raise ValueError(f"retries must not be negative, got {retries}")

    titles = [block.title for block in blocks]
    repeated = sorted({name for name in titles if titles.count(name) > 1})
    if repeated:
        # Readers couldn't tell the rows apart.
        raise ValueError(f"repeated block titles: {repeated}; pass title= to tell them apart")


def _plan(
    blocks: list[Block], context: Mapping[str, Any]
) -> tuple[list[_Call], list[dict[str, _Call]]]:
    """Work out each block's calls and drop duplicates, reporting every missing key at once."""
    calls: list[_Call] = []
    block_calls: list[dict[str, _Call]] = []
    problems: list[str] = []

    for block in blocks:
        sources: dict[str, _Call] = {}
        for key, function in block.sources().items():
            arguments, missing = _arguments_for(function, block.arguments, context)
            if missing:
                problems.append(f"{block.title!r} needs {', '.join(missing)}")
                continue
            call = _find_or_add(calls, function, arguments)
            call.needed_by.append(block.title)
            sources[key] = call
        block_calls.append(sources)

    if problems:
        # Names only: context values can be credentials.
        raise MissingContextError("missing context: " + "; ".join(problems))
    return calls, block_calls


def _arguments_for(
    function: Callable[..., Any], overrides: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    arguments: dict[str, Any] = {}
    missing: list[str] = []
    for parameter in named_parameters(function):
        if parameter.name in overrides:
            arguments[parameter.name] = overrides[parameter.name]
        elif parameter.name in context:
            arguments[parameter.name] = context[parameter.name]
        elif parameter.default is parameter.empty:
            missing.append(parameter.name)
    return arguments, missing


def _find_or_add(
    calls: list[_Call], function: Callable[..., Any], arguments: dict[str, Any]
) -> _Call:
    for call in calls:
        if call.function is function and _same_arguments(call.arguments, arguments):
            return call
    call = _Call(function, arguments)
    calls.append(call)
    return call


def _same_arguments(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Compare by equality, since lists and dicts can't be hashed. Unsure means different: one extra call."""
    if first.keys() != second.keys():
        return False
    try:
        return all(first[name] is second[name] or first[name] == second[name] for name in first)
    except ValueError:  # numpy arrays refuse to compare as a single bool
        return False


def _fetch(
    calls: list[_Call],
    retries: int,
    retry_delay: float,
    retry_on: tuple[type[Exception], ...],
    max_workers: int,
    timeout: float,
) -> None:
    """Run every call, stopping at the first permanent failure."""
    if not calls:
        return

    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="auto-html-report")
    try:
        futures = {
            pool.submit(_run_with_retries, call, retries, retry_delay, retry_on, stop): call
            for call in calls
        }
        for future in as_completed(futures, timeout=timeout):
            futures[future].result = future.result()
    except TimeoutError as error:
        # Only `as_completed` can raise this here: `_run_with_retries` wraps everything in a FetchError.
        raise FetchError(f"gathering data took longer than {timeout:g} s") from error
    finally:
        stop.set()  # also ends any backoff wait in progress
        pool.shutdown(wait=False, cancel_futures=True)


def _run_with_retries(
    call: _Call,
    retries: int,
    retry_delay: float,
    retry_on: tuple[type[Exception], ...],
    stop: threading.Event,
) -> Any:
    name = getattr(call.function, "__qualname__", repr(call.function))
    attempt = 0
    while True:
        attempt += 1
        try:
            return call.function(**call.arguments)
        except retry_on as error:
            if attempt > retries:
                raise _fetch_error(name, call, attempt, error) from error
            delay = retry_delay * 2 ** (attempt - 1)
            logger.warning(
                "%s failed (attempt %d), retrying in %g s: %s", name, attempt, delay, error
            )
            if stop.wait(delay):
                raise _fetch_error(name, call, attempt, error) from error
        except Exception as error:
            raise _fetch_error(name, call, attempt, error) from error


def _fetch_error(name: str, call: _Call, attempts: int, error: Exception) -> FetchError:
    tries = "1 attempt" if attempts == 1 else f"{attempts} attempts"
    needed_by = ", ".join(repr(title) for title in call.needed_by)
    return FetchError(f"{name} failed after {tries}, needed by {needed_by}: {error}")


def _render(block: Block, sources: dict[str, _Call]) -> Row:
    if not sources:
        data = None
    elif set(sources) == {""}:
        data = sources[""].result
    else:
        data = {key: call.result for key, call in sources.items()}

    try:
        html = block.render(data)
        if not isinstance(html, str):
            raise TypeError(f"render must return a string, got {type(html).__name__}")
    except Exception as error:
        logger.exception("rendering %r failed", block.title)
        return Row(block.title, error_cell(error), f"{type(error).__name__}: {error}")
    return Row(block.title, html)

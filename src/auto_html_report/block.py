"""A block is one row of a report: where its data comes from, and how to show it."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any, ClassVar


class Block(ABC):
    """Subclass this, set ``title`` and ``gather``, and write ``render``.

    ``gather`` is a plain function, shared between blocks that need the same data. Its parameter names are
    what the block requires: each is filled from the report context, unless the block's constructor was given
    that argument, so ``MaintenanceTable(tool_id="T-112")`` overrides the context for one block::

        def get_maintenance(tool_id, http):
            return http.get_json(f"/maintenance/{tool_id}", timeout=10)

        class MaintenanceCount(Block):
            title = "Maintenance count"
            gather = get_maintenance

            def render(self, data):
                return str(len(data["records"]))

    For a block that needs several sources, ``gather`` can be a mapping, and ``render`` then receives a dict
    with the same keys: ``gather = {"history": get_maintenance, "lots": get_lots}``. A block that fetches
    nothing leaves ``gather`` unset and receives ``None``.

    Blocks sharing a call receive the same object, so ``render`` must not modify its data. ``render`` returns
    the HTML of the row's Data cell, and anything from outside the process has to go through ``escape`` or
    ``html_table``. If it raises, that row shows an error and the rest of the report still renders.
    """

    title: str = ""
    gather: ClassVar[Any] = None

    def __init__(self, *, title: str | None = None, **arguments: Any) -> None:
        self.title = title or type(self).title
        if not self.title:
            raise TypeError(f"{type(self).__name__} needs a title")

        accepted: set[str] = set()
        for source in self.sources().values():
            if not callable(source):
                raise TypeError(
                    f"{type(self).__name__}.gather must be a function or a dict of functions"
                )
            accepted.update(parameter.name for parameter in named_parameters(source))
        if "self" in accepted:
            raise TypeError(
                f"{type(self).__name__}.gather must be a plain function, not a method taking self: "
                "define it outside the class, so that other blocks can share it"
            )

        unknown = sorted(arguments.keys() - accepted)
        if unknown:
            # Otherwise a misspelled override would silently use the context's value.
            raise TypeError(f"{type(self).__name__} got unknown arguments: {', '.join(unknown)}")
        self.arguments = arguments

    def sources(self) -> dict[str, Callable[..., Any]]:
        """The gather functions by key. A lone function is under ``""``."""
        # Read from the class: through `self`, Python would bind the function as a method.
        gather = type(self).gather
        if gather is None:
            return {}
        return dict(gather) if isinstance(gather, Mapping) else {"": gather}

    @abstractmethod
    def render(self, data: Any) -> str:
        """Return the HTML of this row's Data cell."""


def named_parameters(function: Callable[..., Any]) -> list[inspect.Parameter]:
    """The parameters of ``function``, leaving out ``*args`` and ``**kwargs``."""
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    parameters = inspect.signature(function).parameters.values()
    return [parameter for parameter in parameters if parameter.kind not in variadic]

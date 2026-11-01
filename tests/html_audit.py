"""Auditing a rendered document for anything that would make the browser fetch something.

It parses rather than pattern-matches, because the rule depends on *which* tag an attribute is on.
An ``<a href="https://tickets/…">`` is legitimate cell content; an ``<img src="https://…">`` is not.

Two subtleties learned the hard way, both of which made an earlier version of this module either wrong
or vacuous:

* **CSS comments must be stripped before scanning.** The stylesheet documents the rule "no ``@import``
  and no ``url()``", and a naive scan flags the documentation itself.
* **Whitespace between tags is insignificant; whitespace in text is not.** Normalising a document for
  comparison has to distinguish the two, or reindenting a template looks like a change to the contract.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

#: Tags that exist only to fetch something, or to embed a document that could.
LOADING_TAGS = frozenset(
    {"script", "link", "iframe", "frame", "object", "embed", "audio", "video", "source", "track"}
)

#: Attributes whose value is a URL the browser would fetch.
URL_ATTRIBUTES = frozenset({"src", "srcset", "poster", "data"})

_CSS_COMMENTS = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_css_comments(css: str) -> str:
    """Remove ``/* ... */`` so a scan sees the rules, not the prose describing them."""
    return _CSS_COMMENTS.sub("", css)


def is_embedded_url(value: str) -> bool:
    """True when every URL in a ``src`` or ``srcset`` value is a ``data:`` URI.

    A ``data:`` URI is checked first and returned immediately, because it is a *single* URL that
    contains commas -- its payload is base64. Splitting one on commas and then judging the fragments
    was an earlier bug here, and it flagged every embedded image as external.

    Known limitation: a ``srcset`` whose candidates are themselves ``data:`` URIs is ambiguous, because
    the comma that separates candidates is the same character that separates a data URI's header from
    its payload. Nothing in this project emits ``srcset``, and a report that started to would need this
    function taught the difference.
    """
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("data:"):
        return True
    candidates = [_url_of(part) for part in stripped.split(",")]
    return all(candidate.startswith("data:") for candidate in candidates if candidate)


def _url_of(srcset_candidate: str) -> str:
    """The URL from a ``srcset`` candidate, dropping the descriptor that follows it."""
    tokens = srcset_candidate.strip().split()
    return tokens[0] if tokens else ""


class ResourceAudit(HTMLParser):
    """Collects the tags, their attributes, and the effective text of any inline ``<style>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.stylesheet_text: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {name: (value or "") for name, value in attrs}))
        if tag == "style":
            self._in_style = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.stylesheet_text.append(data)

    def external_loads(self) -> list[str]:
        """Every reason this document would make a network request. Empty means self-contained."""
        problems: list[str] = []

        for tag, attributes in self.tags:
            if tag in LOADING_TAGS:
                problems.append(f"<{tag}>")
            for name, value in attributes.items():
                if name in URL_ATTRIBUTES and not is_embedded_url(value):
                    problems.append(f"<{tag} {name}={value[:60]!r}>")
                if name == "href" and tag != "a":
                    problems.append(f"external href on <{tag}>")
                if name == "style" and _mentions_a_fetch(value):
                    problems.append(f"inline style with url()/import on <{tag}>")

        stylesheet = strip_css_comments(" ".join(self.stylesheet_text))
        if _mentions_a_fetch(stylesheet):
            problems.append("url() or @import in the inlined stylesheet")

        return problems

    def anchors(self) -> list[str]:
        """Every external anchor target, which the containment rule permits."""
        return [
            attributes.get("href", "")
            for tag, attributes in self.tags
            if tag == "a" and "href" in attributes
        ]


def _mentions_a_fetch(css: str) -> bool:
    return "@import" in css or "url(" in css


def audit(html: str) -> ResourceAudit:
    """Parse a document and return its audit."""
    parser = ResourceAudit()
    parser.feed(html)
    return parser

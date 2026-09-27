"""Read-only Pandoc Markdown AST helpers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

from flowmark.pandoc_source import SourceRange, located_nodes, read_source_ast
from flowmark.pandoc_verify import PandocJson, pandoc_executable


class Link(NamedTuple):
    text: str
    url: str
    title: str | None


def walk_elements(value: PandocJson) -> Iterator[dict[str, PandocJson]]:
    """Visit every Pandoc node in document order without changing it."""
    if isinstance(value, dict):
        if isinstance(value.get("t"), str):
            yield value
        for child in value.values():
            yield from walk_elements(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_elements(child)


def stringify_inlines(value: PandocJson) -> str:
    if isinstance(value, list):
        return "".join(stringify_inlines(child) for child in value)
    if not isinstance(value, dict):
        return ""
    kind = value.get("t")
    content = value.get("c")
    if kind == "Str" and isinstance(content, str):
        return content
    if kind in {"Space", "SoftBreak", "LineBreak"}:
        return " "
    if kind in {"Code", "Math", "RawInline"} and isinstance(content, list):
        return content[1] if len(content) == 2 and isinstance(content[1], str) else ""
    if kind in {"Span", "Link", "Image", "Quoted", "Cite"}:
        if isinstance(content, list) and len(content) >= 2:
            return stringify_inlines(content[1])
        return ""
    return stringify_inlines(content)


_EMPTY_TITLE = re.compile(r"\s(?:\"\"|''|\(\))\)$")


def _raw_source(
    source: str, starts: list[int], position: SourceRange | None
) -> str:
    if position is None or position.end.line >= len(starts):
        return ""
    start = starts[position.start.line - 1] + position.start.column - 1
    end = starts[position.end.line - 1] + position.end.column - 1
    return source[start:end] if 0 <= start < end <= len(source) else ""


def extract_links(
    markdown_text: str,
    *,
    include_autolinks: bool = True,
    include_images: bool = False,
) -> list[Link]:
    """Extract links as the configured Pandoc Markdown reader parses them."""
    ast = read_source_ast(markdown_text, pandoc_executable())
    positions = {
        id(located.node): located.source_range for located in located_nodes(ast)
    }
    starts = [0]
    for line in markdown_text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    links: list[Link] = []
    for node in walk_elements(ast):
        kind = node.get("t")
        if kind not in {"Link", "Image"}:
            continue
        if kind == "Image" and not include_images:
            continue
        content = node.get("c")
        if not isinstance(content, list) or len(content) != 3:
            continue
        attributes, inlines, target = content
        if not isinstance(attributes, list) or len(attributes) != 3:
            continue
        classes = attributes[1]
        if (
            kind == "Link"
            and not include_autolinks
            and isinstance(classes, list)
            and "uri" in classes
        ):
            continue
        if not isinstance(target, list) or len(target) != 2:
            continue
        url, raw_title = target
        if not isinstance(url, str) or not isinstance(raw_title, str):
            continue
        raw = _raw_source(markdown_text, starts, positions.get(id(node)))
        title = raw_title if raw_title or _EMPTY_TITLE.search(raw) else None
        links.append(Link(stringify_inlines(inlines), url, title))
    return links


__all__ = ("Link", "walk_elements", "stringify_inlines", "extract_links")

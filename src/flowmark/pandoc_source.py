"""Pandoc Markdown nodes with positions in their authored source."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import cast

from flowmark.linewrapping.text_wrapping import (
    simple_word_splitter,
    wrap_paragraph_lines,
)
from flowmark.pandoc_verify import (
    PANDOC_FORMAT,
    PandocJson,
    PandocParseError,
    check_meaning_preserved,
)


@dataclass(frozen=True)
class SourcePoint:
    line: int
    column: int


@dataclass(frozen=True)
class SourceRange:
    start: SourcePoint
    end: SourcePoint


@dataclass(frozen=True)
class LocatedNode:
    source_range: SourceRange
    node: dict[str, PandocJson]


@dataclass(frozen=True)
class SourceEdit:
    start: int
    end: int
    replacement: str


def read_source_ast(source: str, pandoc_exe: str) -> PandocJson:
    """Parse with the selected Pandoc Markdown reader and its position extension."""
    result = subprocess.run(
        [pandoc_exe, "-f", PANDOC_FORMAT + "+sourcepos", "-t", "json"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PandocParseError(result.stderr.strip())
    return cast(PandocJson, json.loads(result.stdout))


def _point(value: str) -> SourcePoint:
    line, column = value.split(":", maxsplit=1)
    return SourcePoint(int(line), int(column))


def _position(value: PandocJson) -> SourceRange | None:
    if not isinstance(value, dict) or value.get("t") not in {"Div", "Span"}:
        return None
    content = value.get("c")
    if not isinstance(content, list) or len(content) != 2:
        return None
    attr = content[0]
    if not isinstance(attr, list) or len(attr) != 3:
        return None
    attributes = attr[2]
    if not isinstance(attributes, list) or len(attributes) != 1:
        return None
    entry = attributes[0]
    if not isinstance(entry, list) or len(entry) != 2 or entry[0] != "data-pos":
        return None
    raw = entry[1]
    if not isinstance(raw, str):
        return None
    start, end = raw.split("-", maxsplit=1)
    return SourceRange(_point(start), _point(end))


def located_nodes(value: PandocJson) -> list[LocatedNode]:
    """Return source annotated blocks and inlines in document order."""
    nodes: list[LocatedNode] = []

    def visit(item: PandocJson) -> None:
        position = _position(item)
        if position is not None:
            wrapper = cast(dict[str, PandocJson], item)
            content = cast(list[PandocJson], wrapper["c"])
            children = cast(list[PandocJson], content[1])
            if len(children) == 1 and isinstance(children[0], dict):
                nodes.append(LocatedNode(position, children[0]))
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return nodes


def wrap_plain_paragraphs(source: str, width: int, pandoc_exe: str) -> str:
    """Wrap sourced prose paragraphs and preserve all other authored bytes."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") != "Para":
            continue
        inlines = located.node.get("c")
        if not isinstance(inlines, list) or any(
            not isinstance(inline, dict)
            or inline.get("t") not in {"Str", "Space", "SoftBreak"}
            for inline in inlines
        ):
            continue
        first = located.source_range.start
        if first.column != 1 or first.line > len(lines):
            continue
        end_line = min(located.source_range.end.line, len(lines) + 1) - 1
        while end_line >= first.line and not lines[end_line - 1].strip():
            end_line -= 1
        if end_line < first.line:
            continue

        start = starts[first.line - 1]
        end = starts[end_line]
        old = source[start:end]
        wrapped = "\n".join(
            wrap_paragraph_lines(
                old,
                width=width,
                splitter=simple_word_splitter,
                is_markdown=True,
            )
        ) + "\n"
        if wrapped != old:
            edits.append(SourceEdit(start, end, wrapped))

    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if result != source:
        check_meaning_preserved(source, result)
    return result

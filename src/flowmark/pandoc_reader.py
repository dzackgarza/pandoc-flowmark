"""The Pandoc Markdown reader and authored source positions."""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import cast

from flowmark.pandoc_dialect import PANDOC_FORMAT

PandocJson = (
    str | int | float | bool | None | list["PandocJson"] | dict[str, "PandocJson"]
)


class PandocUnavailableError(RuntimeError):
    """The required source-position Pandoc binary is unavailable."""


class PandocParseError(ValueError):
    """Pandoc rejected the Markdown input."""


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
    ancestors: tuple[str, ...]


def pandoc_executable() -> str:
    name = os.environ.get("FLOWMARK_PANDOC", "pandoc-flowmark")
    executable = shutil.which(name)
    if executable is None:
        raise PandocUnavailableError(
            f"Flowmark requires the source-position Pandoc reader `{name}`."
        )
    return executable


@functools.lru_cache(maxsize=32)
def reader_json(pandoc_exe: str, reader_format: str, source: str) -> str:
    """
    Pandoc's JSON reading of `source`. Formatting passes that change nothing leave
    the text as it was, so the next pass reuses this reading.
    """
    result = subprocess.run(
        [pandoc_exe, "-f", reader_format, "-t", "json"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PandocParseError(result.stderr.strip())
    return result.stdout


def read_source_ast(source: str, pandoc_exe: str) -> PandocJson:
    """
    Parse with the configured Pandoc dialect and its position extension, with every
    range settled by `_settle_ranges`.

    Each call returns a new tree, so a caller may change it.
    """
    ast = cast(
        PandocJson,
        json.loads(reader_json(pandoc_exe, PANDOC_FORMAT + "+sourcepos", source)),
    )
    _settle_ranges(ast, source.split("\n"), tabs="\t" in source)
    return ast


@functools.lru_cache(maxsize=32)
def located_source_nodes(source: str, pandoc_exe: str) -> tuple[LocatedNode, ...]:
    """
    `located_nodes` of `read_source_ast(source, pandoc_exe)`, read once for each
    text: a pass that changes nothing leaves the text as it was, so the next pass
    reuses this reading. Every caller shares the nodes, so they must not be changed.
    """
    return tuple(located_nodes(read_source_ast(source, pandoc_exe)))


def _settle_ranges(value: PandocJson, lines: list[str], *, tabs: bool) -> None:
    """
    Rewrite each range in one walk of the tree:

    - Columns become character columns, so a column minus one is an offset into its
      line. Pandoc counts a tab as advancing to the next tab stop.
    - A range that runs past the end of the text ends at the end of the text.
    - A block range that ends at the end of a line's text ends at the start of the
      next line, as Pandoc writes every block that ends before a line break it has
      read, so a block's end line is always the line after its last.
    """
    position = source_position(value)
    if position is not None and isinstance(value, dict):
        start, end = position.start, position.end
        if tabs:
            start, end = _character_point(start, lines), _character_point(end, lines)
        eof = (len(lines), len(lines[-1]) + 1)
        if (start.line, start.column) <= eof < (end.line, end.column):
            end = SourcePoint(*eof)
        if (
            value.get("t") == "Div"
            and end.column > 1
            and end.line <= len(lines)
            and end.column == len(lines[end.line - 1]) + 1
        ):
            end = SourcePoint(end.line + 1, 1)
        if SourceRange(start, end) != position:
            content = cast(list[PandocJson], value["c"])
            attributes = cast(list[PandocJson], cast(list[PandocJson], content[0])[2])
            entry = cast(list[PandocJson], attributes[0])
            entry[1] = f"{start.line}:{start.column}-{end.line}:{end.column}"
    if isinstance(value, dict):
        for child in value.values():
            _settle_ranges(child, lines, tabs=tabs)
    elif isinstance(value, list):
        for child in value:
            _settle_ranges(child, lines, tabs=tabs)


_TAB_STOP = 4
"""Pandoc's tab stop for source positions (`Text.Pandoc.Sources.updateSourcePos`)."""


def _character_column(line: str, column: int) -> int:
    """The 1-based character column of Pandoc's tab-expanded `column` in `line`."""
    expanded = 1
    for index, char in enumerate(line):
        if expanded >= column:
            return index + 1
        if char == "\t":
            expanded += _TAB_STOP - (expanded - 1) % _TAB_STOP
        else:
            expanded += 1
    return len(line) + 1 + max(0, column - expanded)


def _character_point(point: SourcePoint, lines: list[str]) -> SourcePoint:
    """`point` with Pandoc's tab-expanded column as a character column."""
    line = lines[point.line - 1] if point.line <= len(lines) else ""
    return SourcePoint(point.line, _character_column(line, point.column))


def _point(value: str) -> SourcePoint:
    line, column = value.split(":", maxsplit=1)
    return SourcePoint(int(line), int(column))


def source_position(value: PandocJson) -> SourceRange | None:
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

    def visit(item: PandocJson, ancestors: tuple[str, ...]) -> None:
        position = source_position(item)
        if position is not None:
            wrapper = cast(dict[str, PandocJson], item)
            content = cast(list[PandocJson], wrapper["c"])
            children = cast(list[PandocJson], content[1])
            if len(children) == 1 and isinstance(children[0], dict):
                nodes.append(LocatedNode(position, children[0], ancestors))
        if isinstance(item, dict):
            node_type = item.get("t")
            nested_ancestors = (
                ancestors + (node_type,) if isinstance(node_type, str) else ancestors
            )
            for child in item.values():
                visit(child, nested_ancestors)
        elif isinstance(item, list):
            for child in item:
                visit(child, ancestors)

    visit(value, ())
    return nodes

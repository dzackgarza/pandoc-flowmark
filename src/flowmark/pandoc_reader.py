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
    """Parse with the configured Pandoc dialect and its position extension."""
    ast = cast(
        PandocJson,
        json.loads(reader_json(pandoc_exe, PANDOC_FORMAT + "+sourcepos", source)),
    )
    parts = source.split("\n")
    if "\t" in source:
        _to_character_columns(ast, parts)
    _clamp_ranges(ast, SourcePoint(len(parts), len(parts[-1]) + 1))
    _end_blocks_at_line_start(ast, parts)
    return ast


def _end_blocks_at_line_start(value: PandocJson, lines: list[str]) -> None:
    """
    Write a block range that ends at the end of a line's text as ending at the
    start of the next line, as Pandoc writes every block that ends before a line
    break it has read, so a block's end line is always the line after its last.
    """
    position = source_position(value)
    if position is not None and isinstance(value, dict) and value.get("t") == "Div":
        end = position.end
        if (
            end.column > 1
            and end.line <= len(lines)
            and end.column == len(lines[end.line - 1]) + 1
        ):
            content = cast(list[PandocJson], value["c"])
            attributes = cast(list[PandocJson], cast(list[PandocJson], content[0])[2])
            entry = cast(list[PandocJson], attributes[0])
            entry[1] = f"{position.start.line}:{position.start.column}-{end.line + 1}:1"
    if isinstance(value, dict):
        for child in value.values():
            _end_blocks_at_line_start(child, lines)
    elif isinstance(value, list):
        for child in value:
            _end_blocks_at_line_start(child, lines)


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


def _to_character_columns(value: PandocJson, lines: list[str]) -> None:
    """
    Rewrite every range's columns from Pandoc's, which count a tab as advancing
    to the next tab stop, to character columns, so a column minus one is an
    offset into its line.
    """
    position = source_position(value)
    if position is not None and isinstance(value, dict):

        def convert(point: SourcePoint) -> str:
            line = lines[point.line - 1] if point.line <= len(lines) else ""
            return f"{point.line}:{_character_column(line, point.column)}"

        content = cast(list[PandocJson], value["c"])
        attributes = cast(list[PandocJson], cast(list[PandocJson], content[0])[2])
        entry = cast(list[PandocJson], attributes[0])
        entry[1] = f"{convert(position.start)}-{convert(position.end)}"
    if isinstance(value, dict):
        for child in value.values():
            _to_character_columns(child, lines)
    elif isinstance(value, list):
        for child in value:
            _to_character_columns(child, lines)


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


def _clamp_ranges(value: PandocJson, eof: SourcePoint) -> None:
    position = source_position(value)
    if (
        position is not None
        and (position.start.line, position.start.column) <= (eof.line, eof.column)
        and (position.end.line, position.end.column) > (eof.line, eof.column)
        and isinstance(value, dict)
    ):
        content = cast(list[PandocJson], value["c"])
        attributes = cast(list[PandocJson], cast(list[PandocJson], content[0])[2])
        entry = cast(list[PandocJson], attributes[0])
        entry[1] = (
            f"{position.start.line}:{position.start.column}-{eof.line}:{eof.column}"
        )
    if isinstance(value, dict):
        for child in value.values():
            _clamp_ranges(child, eof)
    elif isinstance(value, list):
        for child in value:
            _clamp_ranges(child, eof)


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

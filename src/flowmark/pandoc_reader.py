"""The Pandoc Markdown reader and authored source positions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import cast

PandocJson = (
    str | int | float | bool | None | list["PandocJson"] | dict[str, "PandocJson"]
)

PANDOC_FORMAT = (
    "markdown+fenced_divs+raw_tex+tex_math_dollars"
    "+tex_math_single_backslash+wikilinks_title_after_pipe+autolink_bare_uris"
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


def read_source_ast(source: str, pandoc_exe: str) -> PandocJson:
    """Parse with the configured Pandoc dialect and its position extension."""
    result = subprocess.run(
        [pandoc_exe, "-f", PANDOC_FORMAT + "+sourcepos", "-t", "json"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PandocParseError(result.stderr.strip())
    ast = cast(PandocJson, json.loads(result.stdout))
    parts = source.split("\n")
    _clamp_ranges(ast, SourcePoint(len(parts), len(parts[-1]) + 1))
    return ast


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

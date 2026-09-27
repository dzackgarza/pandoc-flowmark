"""Pandoc Markdown nodes with positions in their authored source."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import cast

from flowmark.linewrapping.line_wrappers import line_wrap_by_sentence
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
    ancestors: tuple[str, ...]


@dataclass(frozen=True)
class SourceEdit:
    start: int
    end: int
    replacement: str


_SPACE = "\ufdd0"
_TAB = "\ufdd1"
_NEWLINE = "\ufdd2"
_HIDE = str.maketrans({" ": _SPACE, "\t": _TAB, "\n": _NEWLINE})
_SHOW = str.maketrans({_SPACE: " ", _TAB: "\t", _NEWLINE: "\n"})


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

    def visit(item: PandocJson, ancestors: tuple[str, ...]) -> None:
        position = _position(item)
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


def _propose_paragraph_edits(
    source: str, width: int, pandoc_exe: str, semantic: bool = False
) -> str:
    """Return sourced paragraph edits for full-document verification."""
    if any(marker in source for marker in (_SPACE, _TAB, _NEWLINE)):
        raise ValueError("The source contains reserved formatter characters")
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        is_list = any(
            ancestor in {"BulletList", "OrderedList"}
            for ancestor in located.ancestors
        )
        is_quote = "BlockQuote" in located.ancestors
        if located.node.get("t") not in {"Para", "Plain"}:
            continue
        if located.node.get("t") == "Plain" and not is_list:
            continue
        first = located.source_range.start
        if first.line > len(lines):
            continue
        if first.column != 1 and (
            not (is_list or is_quote)
            or (is_list and is_quote)
            or "Note" in located.ancestors
        ):
            continue
        prefix_width = first.column - 1
        last = located.source_range.end
        end_line = min(last.line, len(lines))
        if last.line <= len(lines) and last.column < len(
            lines[last.line - 1].rstrip("\r\n")
        ) + 1:
            end_line -= 1
        while end_line >= first.line and not lines[end_line - 1][
            prefix_width:
        ].strip():
            end_line -= 1
        if end_line < first.line:
            continue

        start = starts[first.line - 1]
        end = starts[end_line]
        old = source[start:end]
        if semantic and any(tag in old for tag in ("{%", "{#", "{{", "<!--")):
            continue
        protected = old
        inline_spans: list[tuple[int, int]] = []
        for inline in located_nodes(located.node):
            begin = inline.source_range.start
            finish = inline.source_range.end
            if (
                begin.line > len(lines)
                or finish.line > len(lines) + 1
                or begin.column < 1
                or finish.column < 1
            ):
                raise ValueError("Pandoc returned an invalid inline source range")
            begin_offset = starts[begin.line - 1] + begin.column - 1
            finish_offset = starts[finish.line - 1] + finish.column - 1
            # A Note contains blocks sourced from its definition elsewhere.
            if not (start <= begin_offset < finish_offset <= end):
                continue
            inline_spans.append((begin_offset - start, finish_offset - start))
        if prefix_width and any(
            "\n" in old[begin:finish] for begin, finish in inline_spans
        ):
            continue
        for begin, finish in sorted(inline_spans, reverse=True):
            protected = (
                protected[:begin]
                + protected[begin:finish].translate(_HIDE)
                + protected[finish:]
            )
        if prefix_width:
            paragraph_lines = lines[first.line - 1 : end_line]
            first_prefix = paragraph_lines[0][:prefix_width]
            if "\t" in first_prefix:
                continue
            if is_quote:
                if ">" not in first_prefix or any(
                    character not in " >" for character in first_prefix
                ) or any(
                    not line.startswith(first_prefix)
                    for line in paragraph_lines[1:]
                ):
                    continue
                continuation = first_prefix
            else:
                if any(
                    line[:prefix_width].strip()
                    for line in paragraph_lines[1:]
                ):
                    continue
                continuation = " " * prefix_width
            content = "".join(
                line[prefix_width:]
                for line in protected.splitlines(keepends=True)
            )
            if semantic:
                wrapped = line_wrap_by_sentence(
                    width=width, is_markdown=True, source_preserving=True
                )(
                    content, first_prefix, continuation
                ) + "\n"
            else:
                wrapped_lines = wrap_paragraph_lines(
                    content,
                    width=width,
                    initial_column=prefix_width,
                    subsequent_offset=prefix_width,
                    splitter=simple_word_splitter,
                    is_markdown=True,
                )
                wrapped = (
                    first_prefix
                    + wrapped_lines[0]
                    + "".join("\n" + continuation + line for line in wrapped_lines[1:])
                    + "\n"
                )
            wrapped = wrapped.translate(_SHOW)
            if wrapped != old:
                edits.append(SourceEdit(start, end, wrapped))
            continue
        if semantic:
            wrapped = line_wrap_by_sentence(
                width=width, is_markdown=True, source_preserving=True
            )(
                protected, "", ""
            )
        else:
            wrapped = "\n".join(
                wrap_paragraph_lines(
                    protected,
                    width=width,
                    splitter=simple_word_splitter,
                    is_markdown=True,
                )
            )
        wrapped = wrapped.translate(_SHOW) + "\n"
        if wrapped != old:
            edits.append(SourceEdit(start, end, wrapped))

    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def wrap_plain_paragraphs(
    source: str, width: int, pandoc_exe: str, semantic: bool = False
) -> str:
    """Wrap sourced paragraphs and preserve Pandoc inline source atoms."""
    result = _propose_paragraph_edits(source, width, pandoc_exe, semantic)
    if result != source:
        check_meaning_preserved(source, result)
    return result

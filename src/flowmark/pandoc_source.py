"""Pandoc Markdown nodes with positions in their authored source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from flowmark.formats.options import ListSpacing
from flowmark.linewrapping.line_wrappers import (
    line_wrap_by_sentence,
    line_wrap_to_width,
)
from flowmark.linewrapping.protocols import LineWrapper
from flowmark.linewrapping.tag_handling import (
    add_tag_newline_handling,
    is_tag_only_line,
)
from flowmark.linewrapping.text_wrapping import (
    simple_word_splitter,
    wrap_paragraph_lines,
)
from flowmark.pandoc_reader import (
    PandocJson,
    located_nodes,
    read_source_ast,
    source_position,
)
from flowmark.pandoc_verify import (
    _SUSPENSION_WORDS,  # pyright: ignore[reportPrivateUsage]
    MeaningChangedError,
    check_meaning_preserved,
)
from flowmark.typography.smartquotes import smart_quotes
from flowmark.typography.ellipses import ellipses


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
_ESCAPED_PERIOD = re.compile(r"(?<!\\)\\\.")
_SIMPLE_REFERENCE = re.compile(r"\[([^\[\]\\]+)\](?:\[([^\[\]\\]*)\])?")
_HTML_OPEN_LINE = re.compile(r"^<[A-Za-z][^<>]*>$")
_HTML_CLOSE_LINE = re.compile(r"^</[A-Za-z][A-Za-z0-9-]*>$")


def normalize_sourced_spelling(source: str, pandoc_exe: str) -> str:
    """Apply source spelling changes only where Pandoc validates the edit."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    nodes = located_nodes(read_source_ast(source, pandoc_exe))
    for located in nodes:
        begin = located.source_range.start
        finish = located.source_range.end
        if begin.line >= len(starts):
            continue
        start = starts[begin.line - 1] + begin.column - 1
        kind = located.node.get("t")
        if kind == "Div":
            opener = lines[begin.line - 1][begin.column - 1 :]
            match = re.match(r"^:{3,}(?=\{)", opener)
            if match is not None:
                edits.append(SourceEdit(start + match.end(), start + match.end(), " "))
            continue
        if finish.line >= len(starts):
            continue
        end = starts[finish.line - 1] + finish.column - 1
        if not (0 <= start < end <= len(source)):
            continue
        raw = source[start:end]
        if kind in {"Emph", "Strong"}:
            delimiter = "__" if kind == "Strong" else "_"
            if raw.startswith(delimiter) and raw.endswith(delimiter):
                replacement = (
                    "*" * len(delimiter)
                    + raw[len(delimiter) : -len(delimiter)]
                    + "*" * len(delimiter)
                )
                edits.append(SourceEdit(start, end, replacement))
        elif kind == "Link":
            match = _SIMPLE_REFERENCE.fullmatch(raw)
            if match is not None:
                label, reference = match.groups()
                if reference is None or not reference.strip() or (
                    " ".join(label.split()).casefold()
                    == " ".join(reference.split()).casefold()
                ):
                    replacement = f"[{label}][]"
                    if replacement != raw:
                        edits.append(SourceEdit(start, end, replacement))
        elif kind == "Math" and raw.startswith("$") and not raw.startswith("$$"):
            replacement = raw.replace("\n", " ")
            if replacement != raw:
                edits.append(SourceEdit(start, end, replacement))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if result != source:
        check_meaning_preserved(source, result)

    # A backslash before a period can be removed only when Pandoc still reads
    # the same document. In particular, a period at a wrapped line start can
    # otherwise open a list. The lexical match proposes an edit; Pandoc decides.
    for match in reversed(list(_ESCAPED_PERIOD.finditer(result))):
        candidate = result[: match.start()] + result[match.start() + 1 :]
        try:
            check_meaning_preserved(result, candidate)
        except MeaningChangedError:
            continue
        result = candidate
    return result


def normalize_sourced_html_block_layout(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Join HTML tag lines to adjacent Pandoc prose blocks."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") != "Plain":
            continue
        if any(
            ancestor in {"BulletList", "OrderedList", "Table", "Note", "BlockQuote"}
            for ancestor in located.ancestors
        ):
            continue
        first = located.source_range.start.line - 1
        last = located.source_range.end.line - 1
        if 0 < first < len(lines):
            opening = lines[first - 1].strip()
            if _HTML_OPEN_LINE.fullmatch(opening):
                edits.append(SourceEdit(starts[first] - 1, starts[first], " "))
        if 0 <= last < len(lines) - 1:
            closing = lines[last + 1].strip()
            if _HTML_CLOSE_LINE.fullmatch(closing):
                edits.append(SourceEdit(starts[last + 1] - 1, starts[last + 1], " "))
    result = source
    for edit in sorted(set(edits), key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def _propose_paragraph_edits(
    source: str,
    width: int,
    pandoc_exe: str,
    semantic: bool = False,
    line_wrapper: LineWrapper | None = None,
) -> str:
    """Return sourced paragraph edits for full-document verification."""
    if any(marker in source for marker in (_SPACE, _TAB, _NEWLINE)):
        raise ValueError("The source contains reserved formatter characters")
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    nodes = located_nodes(read_source_ast(source, pandoc_exe))
    raw_block_ends = {
        (node.source_range.end.line, node.source_range.end.column)
        for node in nodes
        if node.node.get("t") == "RawBlock"
    }
    for located in nodes:
        is_list = any(
            ancestor in {"BulletList", "OrderedList"}
            for ancestor in located.ancestors
        )
        is_quote = "BlockQuote" in located.ancestors
        if located.node.get("t") not in {"Para", "Plain"}:
            continue
        if located.node.get("t") == "Plain" and "Table" in located.ancestors:
            continue
        first = located.source_range.start
        if first.line > len(lines):
            continue
        physical_prefix = lines[first.line - 1][: first.column - 1].strip()
        inline_prefix = (
            first.column != 1
            and not (is_list or is_quote)
            and (
                (first.line, first.column) in raw_block_ends
                or _HTML_OPEN_LINE.fullmatch(physical_prefix) is not None
            )
        )
        if first.column != 1 and not (is_list or is_quote or inline_prefix):
            continue
        if "Note" in located.ancestors:
            continue
        prefix_width = first.column - 1
        last = located.source_range.end
        end_line = min(last.line, len(lines))
        end_before_line = last.line <= len(lines) and last.column < len(
            lines[last.line - 1].rstrip("\r\n")
        ) + 1
        partial_end = end_before_line and last.column > 1 and inline_prefix
        if end_before_line and not partial_end:
            end_line -= 1
        # Pandoc can read a table-shaped run as paragraph text when prose touches
        # its header. A standalone parse of the suffix identifies the table's
        # authored rows, which stay on their own lines in this case.
        for index in range(first.line - 1, end_line - 1):
            if "|" not in lines[index] or "|" not in lines[index + 1]:
                continue
            suffix = "".join(lines[index:end_line])
            if any(
                node.node.get("t") == "Table"
                and not node.ancestors
                and node.source_range.start.line == 1
                for node in located_nodes(read_source_ast(suffix, pandoc_exe))
            ):
                end_line = index
                break
        while end_line >= first.line and not lines[end_line - 1][
            prefix_width:
        ].strip():
            end_line -= 1
        if end_line < first.line:
            continue

        start = starts[first.line - 1] + (prefix_width if inline_prefix else 0)
        end = (
            starts[last.line - 1] + last.column - 1
            if partial_end and inline_prefix else starts[end_line]
        )
        old = source[start:end]
        if any(line.strip() == ":::" for line in old.splitlines()):
            continue
        if semantic and any(tag in old for tag in ("{%", "{#", "{{")):
            continue
        protected = old
        inline_spans: list[tuple[int, int]] = []
        paragraph_inlines = located_nodes(located.node)
        has_hard_break = any(
            inline.node.get("t") == "LineBreak" for inline in paragraph_inlines
        )
        if has_hard_break and any(
            inline.node.get("t") == "SoftBreak" for inline in paragraph_inlines
        ):
            continue
        for inline in paragraph_inlines:
            if "Note" in inline.ancestors:
                continue
            if inline.node.get("t") in {"Str", "Space", "SoftBreak"}:
                continue
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
        if inline_prefix:
            leading = " " if protected.startswith(" ") else ""
            content = protected.lstrip(" ")
            wrapper = line_wrapper or (
                line_wrap_by_sentence(
                    width=width, is_markdown=True, source_preserving=True
                ) if semantic else line_wrap_to_width(width=width, is_markdown=False)
            )
            wrapped = wrapper(content, " " * (prefix_width + len(leading)), "")
            wrapped = (
                leading
                + wrapped[prefix_width + len(leading) :]
                + ("\n" if old.endswith("\n") else "")
            ).translate(_SHOW)
            if wrapped != old:
                edits.append(SourceEdit(start, end, wrapped))
            continue
        if prefix_width:
            paragraph_lines = lines[first.line - 1 : end_line]
            first_prefix = paragraph_lines[0][:prefix_width]
            if "\t" in first_prefix:
                continue
            quote_prefix = ""
            if is_quote:
                quote_end = first_prefix.rfind(">")
                if quote_end < 0:
                    continue
                quote_prefix = first_prefix[: quote_end + 1]
                if any(not line.startswith(quote_prefix) for line in paragraph_lines[1:]):
                    continue
                continuation = (
                    quote_prefix + " " * (len(first_prefix) - len(quote_prefix))
                    if is_list else first_prefix
                )
            else:
                if any(
                    line[:prefix_width].strip()
                    for line in paragraph_lines[1:]
                ):
                    continue
                continuation = " " * prefix_width
            protected_lines = protected.splitlines(keepends=True)
            if is_quote and is_list:
                content = protected_lines[0][prefix_width:] + "".join(
                    line[len(quote_prefix) :].lstrip(" ")
                    for line in protected_lines[1:]
                )
            else:
                content = "".join(
                    line[prefix_width:] for line in protected_lines
                )
            if line_wrapper is not None:
                wrapped = line_wrapper(content, first_prefix, continuation) + "\n"
            elif semantic:
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
        if line_wrapper is not None:
            wrapped = line_wrapper(protected, "", "")
        elif any(is_tag_only_line(line) for line in old.splitlines()):
            base_wrapper = (
                line_wrap_by_sentence(
                    width=width, is_markdown=True, source_preserving=True
                )
                if semantic
                else line_wrap_to_width(width=width, is_markdown=False)
            )
            wrapped = add_tag_newline_handling(base_wrapper)(protected, "", "")
        elif semantic:
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
    source: str,
    width: int,
    pandoc_exe: str,
    semantic: bool = False,
    line_wrapper: LineWrapper | None = None,
    verify: bool = True,
) -> str:
    """Wrap sourced paragraphs and preserve Pandoc inline source atoms."""
    result = _propose_paragraph_edits(
        source, width, pandoc_exe, semantic, line_wrapper
    )
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def unbold_sourced_headings(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Remove strong markup when it contains a heading's entire inline content."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") != "Header":
            continue
        content = located.node.get("c")
        if not isinstance(content, list) or len(content) != 3:
            continue
        inlines = content[2]
        if not isinstance(inlines, list) or len(inlines) != 1:
            continue
        inline = inlines[0]
        position = source_position(inline)
        if position is None or not isinstance(inline, dict):
            continue
        wrapper_content = inline.get("c")
        if not isinstance(wrapper_content, list) or len(wrapper_content) != 2:
            continue
        children = wrapper_content[1]
        if (
            not isinstance(children, list)
            or len(children) != 1
            or not isinstance(children[0], dict)
            or children[0].get("t") != "Strong"
        ):
            continue
        if position.start.line > len(lines) or position.end.line > len(lines):
            continue
        start = starts[position.start.line - 1] + position.start.column - 1
        end = starts[position.end.line - 1] + position.end.column - 1
        raw = source[start:end]
        if raw.startswith("**") and raw.endswith("**"):
            edits.append(SourceEdit(start, end, raw[2:-2]))
        elif raw.startswith("__") and raw.endswith("__"):
            edits.append(SourceEdit(start, end, raw[2:-2]))

    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def _inline_edge_text(value: PandocJson, *, last: bool) -> str:
    if not isinstance(value, dict):
        return ""
    node_type = value.get("t")
    content = value.get("c")
    if node_type == "Str" and isinstance(content, str):
        return content
    if node_type == "Math":
        return "$"
    if node_type == "Span" and isinstance(content, list) and len(content) == 2:
        content = content[1]
    if not isinstance(content, list):
        return ""
    children = reversed(content) if last else iter(content)
    for child in children:
        result = _inline_edge_text(child, last=last)
        if result:
            return result
    return ""


def join_sourced_hyphen_breaks(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> tuple[str, int]:
    """Close soft breaks after a hyphen when the following Pandoc inline joins it."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []

    def visit(value: PandocJson) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                position = source_position(item)
                if position is not None and 0 < index < len(value) - 1:
                    wrapper = cast(dict[str, PandocJson], item)
                    content = cast(list[PandocJson], wrapper["c"])
                    if content[1] == [{"t": "SoftBreak"}]:
                        before = _inline_edge_text(value[index - 1], last=True)
                        after = _inline_edge_text(value[index + 1], last=False)
                        first_word = after.split()[0] if after.split() else ""
                        joins = (
                            before.endswith("-")
                            and bool(after)
                            and first_word.strip(".,;:!?").lower()
                            not in _SUSPENSION_WORDS
                            and (
                                after[0].isdigit()
                                or after[0].islower()
                                or after[0] in "$\\"
                            )
                        )
                        if joins and position.end.line <= len(lines) + 1:
                            start = (
                                starts[position.start.line - 1]
                                + position.start.column
                                - 1
                            )
                            end = (
                                starts[position.end.line - 1]
                                + position.end.column
                                - 1
                            )
                            if source[start:end] in {"\n", "\r\n"}:
                                edits.append(SourceEdit(start, end, ""))
                visit(item)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)

    visit(read_source_ast(source, pandoc_exe))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result, len(edits)


def set_sourced_list_spacing(
    source: str, pandoc_exe: str, *, loose: bool, verify: bool = True
) -> str:
    """Change gaps between items identified by Pandoc list nodes."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        kind = located.node.get("t")
        if kind not in {"BulletList", "OrderedList"}:
            continue
        quoted = "BlockQuote" in located.ancestors
        noted = "Note" in located.ancestors
        nested = any(
            ancestor in {"BulletList", "OrderedList"}
            for ancestor in located.ancestors
        )
        if loose and nested:
            line_index = located.source_range.start.line - 1
            if 0 < line_index < len(lines) and lines[line_index - 1].strip():
                blank = ">\n" if quoted else "\n"
                edits.append(SourceEdit(starts[line_index], starts[line_index], blank))
        content = located.node.get("c")
        if not isinstance(content, list):
            continue
        items = content if kind == "BulletList" else content[1]
        if not isinstance(items, list):
            continue
        for item in items[1:]:
            if not isinstance(item, list) or not item:
                continue
            position = source_position(item[0])
            if position is None or not (1 < position.start.line <= len(lines)):
                continue
            line_index = position.start.line - 1
            blank = "\n"
            if quoted:
                marker_prefix = lines[line_index][: position.start.column - 1]
                quote_end = marker_prefix.rfind(">")
                if quote_end < 0:
                    continue
                blank = marker_prefix[: quote_end + 1] + "\n"
            elif noted:
                marker_prefix = lines[line_index][: position.start.column - 1]
                blank = marker_prefix[: len(marker_prefix) - len(marker_prefix.lstrip(" \t"))] + "\n"
            previous = line_index - 1
            while previous >= 0 and not lines[previous].strip(
                " \t\r\n>" if quoted else " \t\r\n"
            ):
                previous -= 1
            gap_start = starts[previous + 1]
            gap_end = starts[line_index]
            if loose and gap_start == gap_end:
                edits.append(SourceEdit(gap_end, gap_end, blank))
            elif loose and gap_end - gap_start > len(blank):
                edits.append(SourceEdit(gap_start, gap_end, blank))
            elif not loose and gap_start != gap_end:
                edits.append(SourceEdit(gap_start, gap_end, ""))

    result = source
    for edit in sorted(set(edits), key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def normalize_sourced_list_indentation(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Use Pandoc-safe indentation for nested lists."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    shifts: dict[int, tuple[int, int]] = {}
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") not in {"BulletList", "OrderedList"}:
            continue
        if any(ancestor in {"BlockQuote", "Note"} for ancestor in located.ancestors):
            continue
        depth = sum(
            ancestor in {"BulletList", "OrderedList"}
            for ancestor in located.ancestors
        )
        if depth == 0:
            continue
        first = located.source_range.start.line - 1
        last = min(located.source_range.end.line - 1, len(lines))
        if not (0 <= first < len(lines)):
            continue
        indent = len(lines[first]) - len(lines[first].lstrip(" "))
        delta = indent - 4 * depth
        if delta <= 0:
            continue
        for index in range(first, last):
            if index not in shifts or depth > shifts[index][0]:
                shifts[index] = (depth, delta)
    edits: list[SourceEdit] = []
    for index, (_depth, delta) in shifts.items():
        if lines[index].startswith(" " * delta):
            edits.append(SourceEdit(starts[index], starts[index] + delta, ""))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def normalize_sourced_indented_code(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Write Pandoc indented code blocks with safe backtick fences."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") != "CodeBlock" or located.ancestors:
            continue
        first = located.source_range.start.line - 1
        last = located.source_range.end.line - 1
        if not (0 <= first < len(lines) and first < last <= len(lines)):
            continue
        if not lines[first].startswith("    "):
            continue
        content = located.node.get("c")
        if not isinstance(content, list) or len(content) != 2:
            continue
        code = content[1]
        if not isinstance(code, str):
            continue
        longest = max(
            (len(run.group(0)) for run in re.finditer(r"`+", code)),
            default=0,
        )
        fence = "`" * max(3, longest + 1)
        old = source[starts[first] : starts[last]]
        suffix = "\n\n" if old.endswith("\n\n") else "\n"
        replacement = f"{fence}\n{code}\n{fence}{suffix}"
        edits.append(SourceEdit(starts[first], starts[last], replacement))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def normalize_sourced_blank_gaps(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Keep one empty separator line between authored blocks."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    protected: set[int] = set()
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") not in {"CodeBlock", "RawBlock", "Math"}:
            continue
        first = located.source_range.start.line - 1
        last = min(located.source_range.end.line - 1, len(lines))
        while last > first and not lines[last - 1].strip():
            last -= 1
        protected.update(
            range(first, last)
        )
    edits: list[SourceEdit] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() or index in protected:
            index += 1
            continue
        start = index
        while index < len(lines) and not lines[index].strip() and index not in protected:
            index += 1
        if index - start > 1:
            edits.append(SourceEdit(starts[start], starts[index], "\n"))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def normalize_sourced_quote_blank_lines(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Write a space after the marker of an empty blockquote line."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: set[SourceEdit] = set()
    nodes = located_nodes(read_source_ast(source, pandoc_exe))
    list_lines = {
        index
        for node in nodes
        if node.node.get("t") in {"BulletList", "OrderedList"}
        and "BlockQuote" in node.ancestors
        for index in range(
            node.source_range.start.line - 1,
            min(node.source_range.end.line - 1, len(lines)),
        )
    }
    for located in nodes:
        if located.node.get("t") != "BlockQuote":
            continue
        first = located.source_range.start.line - 1
        last = min(located.source_range.end.line - 1, len(lines))
        for index in range(first, last):
            raw = lines[index].rstrip("\r\n")
            if index not in list_lines and re.fullmatch(r"[ >]*>", raw):
                edits.add(SourceEdit(starts[index] + len(raw), starts[index] + len(raw), " "))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def apply_sourced_smart_quotes(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Apply prose quote style only at inline text owned by Pandoc."""
    styled = smart_quotes(source)
    if len(styled) != len(source):
        raise ValueError("Smart quote conversion changed source length")
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    eligible: set[int] = set()
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        kind = located.node.get("t")
        if kind not in {"Str", "Quoted"}:
            continue
        begin = located.source_range.start
        finish = located.source_range.end
        if begin.line > len(lines) or finish.line > len(lines):
            continue
        start = starts[begin.line - 1] + begin.column - 1
        end = starts[finish.line - 1] + finish.column - 1
        if not (0 <= start < end <= len(source)):
            continue
        if kind == "Str":
            eligible.update(range(start, end))
        elif source[start] in "'\"" and source[end - 1] == source[start]:
            eligible.update((start, end - 1))

    result = list(source)
    for index in eligible:
        if source[index] in "'\"" and styled[index] in "‘’“”":
            result[index] = styled[index]
    formatted = "".join(result)
    if verify and formatted != source:
        check_meaning_preserved(source, formatted)
    return formatted


def apply_sourced_ellipses(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Style ellipses only where Pandoc decoded literal prose to an ellipsis."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") != "Str" or "…" not in str(
            located.node.get("c", "")
        ):
            continue
        begin = located.source_range.start
        finish = located.source_range.end
        if begin.line > len(lines) or finish.line > len(lines):
            continue
        start = starts[begin.line - 1] + begin.column - 1
        end = starts[finish.line - 1] + finish.column - 1
        if not (0 <= start < end <= len(source)) or "..." not in source[start:end]:
            continue
        left = start
        while left > 0 and source[left - 1] in " \t":
            left -= 1
        if left > 0 and source[left - 1] not in "\r\n":
            left -= 1
        right = end
        while right < len(source) and source[right] == ".":
            right += 1
        while right < len(source) and source[right] in " \t":
            right += 1
        if right < len(source) and source[right] not in "\r\n":
            right += 1
        prefix, suffix = source[left:start], source[end:right]
        styled = ellipses(source[left:right])
        if styled.startswith(prefix) and styled.endswith(suffix):
            replacement = styled[len(prefix) : len(styled) - len(suffix) or None]
            if replacement != source[start:end]:
                edits.append(SourceEdit(start, end, replacement))

    result = source
    for edit in sorted(set(edits), key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def set_sourced_heading_spacing(
    source: str, pandoc_exe: str, *, verify: bool = True
) -> str:
    """Separate Pandoc headings from the next authored block."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
        if located.node.get("t") != "Header":
            continue
        next_line = located.source_range.end.line
        if not (1 < next_line <= len(lines)):
            continue
        previous = lines[next_line - 2].rstrip("\r\n")
        if not previous.strip():
            continue
        if previous.endswith("\\") or previous.endswith("  "):
            continue
        if not lines[next_line - 1].strip():
            continue
        prefix = lines[next_line - 1][: located.source_range.end.column - 1]
        if "BlockQuote" in located.ancestors and ">" in prefix:
            blank = prefix[: prefix.rfind(">") + 1] + "\n"
        elif any(
            ancestor in {"BulletList", "OrderedList", "Note"}
            for ancestor in located.ancestors
        ):
            blank = prefix[: len(prefix) - len(prefix.lstrip(" \t"))] + "\n"
        else:
            blank = "\n"
        offset = starts[next_line - 1]
        edits.append(SourceEdit(offset, offset, blank))

    result = source
    for edit in sorted(set(edits), key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result


def format_sourced_markdown(
    source: str,
    pandoc_exe: str,
    *,
    width: int,
    semantic: bool,
    cleanups: bool,
    smartquotes: bool,
    ellipses: bool,
    list_spacing: ListSpacing,
    line_wrapper: LineWrapper | None = None,
    verify: bool = True,
) -> tuple[str, int]:
    """Run the supported formatting edits through one Pandoc source map."""
    result = normalize_sourced_spelling(source, pandoc_exe)
    result = normalize_sourced_html_block_layout(result, pandoc_exe, verify=False)
    joined = 0
    if cleanups:
        result = unbold_sourced_headings(result, pandoc_exe, verify=False)
        result, joined = join_sourced_hyphen_breaks(result, pandoc_exe, verify=False)
    if smartquotes:
        result = apply_sourced_smart_quotes(result, pandoc_exe, verify=False)
    if ellipses:
        result = apply_sourced_ellipses(result, pandoc_exe, verify=False)
    if width > 0 or semantic or line_wrapper is not None:
        result = wrap_plain_paragraphs(
            result, width, pandoc_exe, semantic, line_wrapper, verify=False
        )
    if list_spacing is not ListSpacing.preserve:
        result = set_sourced_list_spacing(
            result, pandoc_exe, loose=list_spacing is ListSpacing.loose, verify=False
        )
        result = normalize_sourced_list_indentation(result, pandoc_exe, verify=False)
    result = normalize_sourced_indented_code(result, pandoc_exe, verify=False)
    result = normalize_sourced_blank_gaps(result, pandoc_exe, verify=False)
    result = normalize_sourced_quote_blank_lines(result, pandoc_exe, verify=False)
    result = set_sourced_heading_spacing(result, pandoc_exe, verify=False)
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result, joined

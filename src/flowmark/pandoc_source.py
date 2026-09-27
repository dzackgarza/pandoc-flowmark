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
    preprocess_tag_block_spacing,
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


def normalize_sourced_spelling(source: str, pandoc_exe: str) -> str:
    """Apply source spelling changes only where Pandoc validates the edit."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    for located in located_nodes(read_source_ast(source, pandoc_exe)):
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
        if kind == "Link":
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
        if any(line.strip() == ":::" for line in old.splitlines()):
            continue
        if semantic and any(tag in old for tag in ("{%", "{#", "{{", "<!--")):
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
            elif not loose and gap_start != gap_end:
                edits.append(SourceEdit(gap_start, gap_end, ""))

    result = source
    for edit in sorted(set(edits), key=lambda item: item.start, reverse=True):
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
    result = preprocess_tag_block_spacing(result)
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
    result = set_sourced_heading_spacing(result, pandoc_exe, verify=False)
    if verify and result != source:
        check_meaning_preserved(source, result)
    return result, joined

"""Pandoc Markdown nodes with positions in their authored source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import accumulate
from typing import cast

from flowmark.formats.options import (
    FormatOptions,
    ListSpacing,
    Pass,
    Plain,
    Semantic,
    Width,
)
from flowmark.linewrapping.line_wrappers import (
    line_wrap_by_sentence,
    line_wrap_to_width,
    markdown_line_wrap_to_width,
)
from flowmark.linewrapping.protocols import LineWrapper
from flowmark.linewrapping.tag_handling import (
    add_tag_newline_handling,
    is_tag_only_line,
)
from flowmark.linewrapping.text_wrapping import markdown_escape_word
from flowmark.pandoc_reader import (
    located_source_nodes,
    LocatedNode,
    PandocJson,
    located_nodes,
    SourcePoint,
    character_point,
    read_source_ast,
    source_position,
)
from flowmark.pandoc_verify import (
    GFM_ALERT_TYPES,
    SUSPENSION_WORDS,
    MeaningChangedError,
    check_meaning_preserved,
)
from flowmark.preflight import Finding, opens_fence
from flowmark.typography.ellipses import ellipses
from flowmark.typography.smartquotes import smart_quotes


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
_PIPE_DELIMITER_ROW = re.compile(r"\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*")
# Inline nodes the wrapper never breaks. Emphasis, strong, strikeout, and quoted
# text wrap between their words like plain prose.
_WRAP_ATOMS = frozenset(
    {"Code", "Math", "RawInline", "Link", "Image", "Cite", "Note", "Span", "LineBreak"}
)
# Atoms whose descendants are hidden with them. Every located inline sits in a
# `data-pos` Span wrapper, so a Span ancestor says nothing.
_NESTING_ATOMS = _WRAP_ATOMS - {"Span"}
_JOINED_ATOMS = frozenset({"Code", "Link", "Image", "Cite", "Note", "Span"})
# Indentation, quote markers, and one list marker with the whitespace after it.
_LIST_LINE_PREFIX = re.compile(
    r"[ \t]*(?:>[ \t]*)*(?:(?:[-+*]|\(?(?:\d+|[A-Za-z]+|#)[.)])[ \t]+)?"
)
# The text before a list item's content: indentation, a marker, and one to four
# spaces. Five or more spaces after a marker start indented code instead.
_MARKER_SPACING = re.compile(r"( *)([-+*]|\(?(?:\d+|[A-Za-z]+|#)[.)])( {1,4})")
# A link reference definition line; a footnote definition may follow it directly.
_LINK_DEFINITION = re.compile(r" {0,3}\[(?!\^)[^\]]+\]:[ \t]")
# A footnote definition's label and the whitespace after it.
_NOTE_DEFINITION = re.compile(r"(\[\^[^\]\s]+\]:)[ \t]*")
# A task-list box at the start of a list item's text, with the space after it.
_TASK_BOX = re.compile(r"^\[[ xX]\] ")
# A backslash before a space, not itself escaped.
_ESCAPED_SPACE = re.compile(r"(?<!\\)((?:\\\\)*)\\ ")
_TRAILING_SPACE = re.compile(r"[ \t]+(?=\n)")
# A line break in atom text other than code, with the indentation and quote
# markers after it: all of it reads as one space.
_TEXT_BREAK = re.compile(r"\n[ \t>]*")
_ALERT_MARKER = re.compile(r"\[!([A-Za-z][\w-]*)\][+-]?(?:[ \t][^\n]*)?\n")


def expand_sourced_leading_tabs(source: str, pandoc_exe: str) -> str:
    """
    Write tabs in a line's indentation, quote markers, and list marker as spaces,
    to Pandoc's tab stop of 4 columns.

    Code and raw blocks keep their tabs, which are content there, and so does a
    line that begins inside a code, math, or raw span continued from the line
    before.
    """
    lines = source.splitlines(keepends=True)
    kept: set[int] = set()
    for node in located_source_nodes(source, pandoc_exe):
        kind = node.node.get("t")
        first = node.source_range.start.line - 1
        last = min(node.source_range.end.line, len(lines))
        if kind in {"CodeBlock", "RawBlock"}:
            kept.update(range(first, last))
        elif kind in {"Math", "Code", "RawInline"}:
            kept.update(range(first + 1, last))
    result: list[str] = []
    for index, line in enumerate(lines):
        prefix = _LIST_LINE_PREFIX.match(line)
        if index not in kept and prefix is not None and "\t" in prefix.group(0):
            line = prefix.group(0).expandtabs(4) + line[prefix.end() :]
        result.append(line)
    expanded = "".join(result)
    return expanded


def normalize_sourced_spelling(source: str, pandoc_exe: str) -> str:
    """Apply source spelling changes only where Pandoc validates the edit."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    nodes = located_source_nodes(source, pandoc_exe)
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
                if (
                    reference is None
                    or not reference.strip()
                    or (
                        " ".join(label.split()).casefold()
                        == " ".join(reference.split()).casefold()
                    )
                ):
                    replacement = f"[{label}][]"
                    if replacement != raw:
                        edits.append(SourceEdit(start, end, replacement))
        elif kind == "LineBreak" and raw.endswith("\n") and not raw.strip():
            # Trailing spaces are an invisible hard break; a backslash shows it.
            edits.append(SourceEdit(start, end, "\\\n"))
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
    # otherwise open a list. The lexical match proposes an edit; Pandoc decides,
    # first for all of them at once and, if that changes the reading, one by one.
    unescaped = _ESCAPED_PERIOD.sub(".", result)
    if unescaped != result:
        try:
            check_meaning_preserved(result, unescaped)
        except MeaningChangedError:
            pass
        else:
            return unescaped
    for match in reversed(list(_ESCAPED_PERIOD.finditer(result))):
        candidate = result[: match.start()] + result[match.start() + 1 :]
        try:
            check_meaning_preserved(result, candidate)
        except MeaningChangedError:
            continue
        result = candidate
    return result


def normalize_sourced_html_block_layout(source: str, pandoc_exe: str) -> str:
    """Join HTML tag lines to adjacent Pandoc prose blocks."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") != "Plain":
            continue
        if any(
            ancestor in {"BulletList", "OrderedList", "Table", "Note", "BlockQuote"}
            for ancestor in located.ancestors
        ):
            continue
        first = located.source_range.start.line - 1
        # A block that ends at a line start ends on the line before it.
        end = located.source_range.end
        last = end.line - 1 - (1 if end.column == 1 else 0)
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
    return result


def _span_kind(node: dict[str, PandocJson]) -> str:
    """
    How the wrapper treats an inline atom: `code` or other `joined` text written
    on one line, `display` math, or kept as written (`other`).

    Pandoc reads a line break inside code, link, image, citation, span, and note
    text as one space, so those atoms are written on one line.
    """
    if node.get("t") == "Code":
        return "code"
    if node.get("t") in _JOINED_ATOMS:
        return "joined"
    content = node.get("c")
    if (
        node.get("t") == "Math"
        and isinstance(content, list)
        and isinstance(content[0], dict)
        and content[0].get("t") == "DisplayMath"
    ):
        return "display"
    return "other"


def _set_off_display_math(wrapped: str, display_math: list[str], indent: str) -> str:
    """
    Start each multi-line display math span on its own line and end the line
    after it.

    Breaking a wrapped line only shortens lines, so the width still holds.
    """
    for hidden in display_math:
        wrapped = wrapped.replace(" " + hidden, "\n" + indent + hidden)
        wrapped = wrapped.replace(hidden + " ", hidden + "\n" + indent)
    return wrapped


@dataclass(frozen=True)
class ParagraphWrappers:
    """The line wrappers for the kinds of paragraph text."""

    prose: LineWrapper
    tagged: LineWrapper
    """For a paragraph with a line that holds only a tag."""
    inline: LineWrapper
    """For text that follows an HTML tag or raw block on its first line."""


def paragraph_wrappers(wrap: Width | Semantic) -> ParagraphWrappers:
    """
    The wrappers for `wrap`. The Markdown word splitter keeps template tags, HTML
    tags, and link syntax whole; the width wrapper also keeps the lines around
    tag-only lines.
    """
    match wrap:
        case Width(width):
            prose = markdown_line_wrap_to_width(width=width)
            return ParagraphWrappers(prose, prose, line_wrap_to_width(width=width))
        case Semantic(width):
            sentences = line_wrap_by_sentence(
                width=width, escape_word=markdown_escape_word
            )
            return ParagraphWrappers(
                sentences, add_tag_newline_handling(sentences), sentences
            )


def _wrap_segment(
    text: str, first_indent: str, indent: str, wrapper: LineWrapper
) -> str:
    """Wrap one run of paragraph text, without a final newline."""
    if not text.strip():
        # Consecutive hard breaks leave a line with nothing but its break.
        return first_indent
    return wrapper(text, first_indent, indent)


def _wrap_text(text: str, first_indent: str, indent: str, wrapper: LineWrapper) -> str:
    """
    Wrap paragraph text, without a final newline. In a paragraph with a backslash
    hard break, the author set the lines, so each line wraps on its own.
    """
    segments = text.rstrip("\n").split("\n") if "\\\n" in text else [text]
    return "\n".join(
        _wrap_segment(segment, first_indent if index == 0 else indent, indent, wrapper)
        for index, segment in enumerate(segments)
    )


def wrap_plain_paragraphs(
    source: str, pandoc_exe: str, wrappers: ParagraphWrappers
) -> str:
    """Wrap sourced paragraphs and preserve Pandoc inline source atoms."""
    if any(marker in source for marker in (_SPACE, _TAB, _NEWLINE)):
        raise ValueError("The source contains reserved formatter characters")
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    nodes = located_source_nodes(source, pandoc_exe)
    raw_block_ends = {
        (node.source_range.end.line, node.source_range.end.column)
        for node in nodes
        if node.node.get("t") == "RawBlock"
    }
    # A paragraph still holding a line that opens a list on its own is one whose
    # list could not be set off (`separate_sourced_lazy_lists`); wrapping would
    # run the list into prose, so it is left as written.
    top_paragraphs = [
        node
        for node in nodes
        if node.node.get("t") == "Para" and set(node.ancestors) <= {"Div"}
    ]
    interrupting = _interrupting_list_lines(lines, top_paragraphs, pandoc_exe)
    unwrapped_paragraphs = {
        (node.source_range.start.line, node.source_range.start.column)
        for node in top_paragraphs
        if any(
            node.source_range.start.line < line < node.source_range.end.line
            for line in interrupting
        )
    }
    for located in nodes:
        is_list = any(
            ancestor in {"BulletList", "OrderedList"} for ancestor in located.ancestors
        )
        is_quote = "BlockQuote" in located.ancestors
        if located.node.get("t") not in {"Para", "Plain"}:
            continue
        if located.node.get("t") == "Plain" and "Table" in located.ancestors:
            continue
        if (
            located.source_range.start.line,
            located.source_range.start.column,
        ) in unwrapped_paragraphs:
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
        # A footnote definition's own blocks wrap under a four-space indent; a
        # block nested deeper in a note, or an inline note, is left as written.
        is_note = located.ancestors[-1:] == ("Note",)
        if "Note" in located.ancestors and not is_note:
            continue
        note_marker = _NOTE_DEFINITION.match(lines[first.line - 1]) if is_note else None
        if is_note and note_marker is None and first.column != 5:
            continue
        if first.column != 1 and not (is_list or is_quote or inline_prefix or is_note):
            continue
        prefix_width = (
            note_marker.end() if note_marker is not None else first.column - 1
        )
        last = located.source_range.end
        end_line = min(last.line, len(lines))
        end_before_line = (
            last.line <= len(lines)
            and last.column < len(lines[last.line - 1].rstrip("\r\n")) + 1
        )
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
                for node in located_source_nodes(suffix, pandoc_exe)
            ):
                end_line = index
                break
        # A paragraph ends at a blank line, whatever range Pandoc reports.
        for index in range(first.line, end_line):
            if not lines[index].strip(" \t>\r\n"):
                end_line = index
                break
        while end_line >= first.line and not lines[end_line - 1][prefix_width:].strip():
            end_line -= 1
        if end_line < first.line:
            continue

        start = starts[first.line - 1] + (prefix_width if inline_prefix else 0)
        end = (
            starts[last.line - 1] + last.column - 1
            if partial_end and inline_prefix
            else starts[end_line]
        )
        old = source[start:end]
        if any(line.strip() == ":::" for line in old.splitlines()):
            continue
        # A fence-shaped line Pandoc reads as paragraph text is a code block to
        # CommonMark; wrapping would run the code into prose.
        if any(opens_fence(line[prefix_width:]) for line in old.splitlines()):
            continue
        protected = old
        inline_spans: list[tuple[int, int, str]] = []
        paragraph_inlines = located_nodes(located.node)
        wrapper = (
            wrappers.tagged
            if any(is_tag_only_line(line) for line in old.splitlines())
            else wrappers.prose
        )
        for inline in paragraph_inlines:
            if "Note" in inline.ancestors:
                continue
            # Pandoc reads the space after an abbreviation such as `e.g.`, and an
            # escaped space, as a non-breaking space inside one `Str`; a line
            # break there would read as an ordinary space.
            nonbreaking = inline.node.get("t") == "Str" and "\u00a0" in str(
                inline.node["c"]
            )
            if inline.node.get("t") not in _WRAP_ATOMS and not nonbreaking:
                continue
            if any(ancestor in _NESTING_ATOMS for ancestor in inline.ancestors):
                continue
            span_start = inline.source_range.start
            span_end = inline.source_range.end
            if (
                span_start.line > len(lines)
                or span_end.line > len(lines) + 1
                or span_start.column < 1
                or span_end.column < 1
            ):
                raise ValueError("Pandoc returned an invalid inline source range")
            begin_offset = starts[span_start.line - 1] + span_start.column - 1
            finish_offset = starts[span_end.line - 1] + span_end.column - 1
            # A Note contains blocks sourced from its definition elsewhere.
            if not (start <= begin_offset < finish_offset <= end):
                continue
            inline_spans.append(
                (begin_offset - start, finish_offset - start, _span_kind(inline.node))
            )
        if prefix_width and any(
            "\n" in old[begin:finish] and kind in {"other", "display"}
            for begin, finish, kind in inline_spans
        ):
            continue
        # Pandoc reads a line break in a joined atom as one space, so writing the
        # space keeps the atom on one line. In code, only the container's
        # prefix after the break goes with it: the quote markers, each with one
        # optional space, then the list indentation up to the content column.
        physical = lines[first.line - 1][:prefix_width]
        quote_width = physical.rfind(">") + 1
        if quote_width and physical[quote_width : quote_width + 1] == " ":
            quote_width += 1
        code_break = re.compile(
            r"\n"
            + (r"(?: {0,3}> ?)*" if is_quote else "")
            + f" {{0,{prefix_width - quote_width}}}"
        )
        display_math: list[str] = []
        for begin, finish, kind in sorted(inline_spans, reverse=True):
            span = protected[begin:finish]
            if kind == "code":
                span = code_break.sub(" ", span)
            elif kind == "joined":
                span = _TEXT_BREAK.sub(" ", span)
            hidden = span.translate(_HIDE)
            if kind == "display" and "\n" in span:
                # Display math written over several lines keeps its authored
                # lines, less trailing spaces, and is set off from the prose.
                hidden = _TRAILING_SPACE.sub("", span).translate(_HIDE)
                display_math.append(hidden)
            protected = protected[:begin] + hidden + protected[finish:]
        # A backslash-escaped space is a non-breaking space to Pandoc.
        protected = _ESCAPED_SPACE.sub(
            lambda match: match.group(1) + "\\" + _SPACE, protected
        )
        if inline_prefix:
            leading = " " if protected.startswith(" ") else ""
            content = protected.lstrip(" ")
            wrapped = wrappers.inline(content, " " * (prefix_width + len(leading)), "")
            # Text that ends before an inline closing tag keeps the space
            # that separates it from the tag.
            wrapped = (
                leading
                + wrapped[prefix_width + len(leading) :]
                + ("\n" if old.endswith("\n") else "")
                + (" " if old.endswith(" ") else "")
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
                if any(
                    not line.startswith(quote_prefix) for line in paragraph_lines[1:]
                ):
                    continue
                continuation = (
                    quote_prefix + " " * (len(first_prefix) - len(quote_prefix))
                    if is_list
                    else first_prefix
                )
            elif note_marker is not None:
                first_prefix = note_marker.group(1) + " "
                continuation = " " * 4
            else:
                continuation = " " * prefix_width
            protected_lines = protected.splitlines(keepends=True)
            if is_quote and is_list:
                content = protected_lines[0][prefix_width:] + "".join(
                    line[len(quote_prefix) :].lstrip(" ")
                    for line in protected_lines[1:]
                )
            elif is_quote:
                content = "".join(line[prefix_width:] for line in protected_lines)
            else:
                # Pandoc ignores a paragraph line's leading spaces, including a
                # lazy continuation line's missing indentation.
                content = protected_lines[0][prefix_width:] + "".join(
                    line.lstrip(" ") for line in protected_lines[1:]
                )
            # A task box is one only when a space follows it on its line, so it
            # is kept together with the word after it.
            if is_list:
                content = _TASK_BOX.sub(
                    lambda match: match.group(0).translate(_HIDE), content, count=1
                )
            # A GFM alert or Obsidian callout marker keeps its own line; the
            # paragraph text below it wraps as usual.
            alert_header = ""
            marker = _ALERT_MARKER.match(content) if is_quote and not is_list else None
            if marker is not None:
                alert_type = marker.group(1)
                if alert_type.lower() in GFM_ALERT_TYPES:
                    alert_type = alert_type.upper()
                alert_header = (
                    first_prefix
                    + f"[!{alert_type}]"
                    + marker.group(0)[len(marker.group(1)) + 3 :]
                )
                content = content[marker.end() :]
                first_prefix = continuation
            wrapped = alert_header
            if content.strip():
                wrapped += (
                    _wrap_text(content, first_prefix, continuation, wrapper) + "\n"
                )
            wrapped = _set_off_display_math(wrapped, display_math, continuation)
            wrapped = wrapped.translate(_SHOW)
            if wrapped != old:
                edits.append(SourceEdit(start, end, wrapped))
            continue
        wrapped = _wrap_text(protected, "", "", wrapper)
        wrapped = _set_off_display_math(wrapped, display_math, "")
        wrapped = wrapped.translate(_SHOW) + "\n"
        if wrapped != old:
            edits.append(SourceEdit(start, end, wrapped))

    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def unbold_sourced_headings(source: str, pandoc_exe: str) -> str:
    """Remove strong markup when it contains a heading's entire inline content."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
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
    return result


def _inline_children(value: PandocJson) -> list[PandocJson] | str:
    """
    The text of a `Str` inline (`$` for math), or the child inlines of any other
    inline, in order.
    """
    if not isinstance(value, dict):
        return []
    node_type = value.get("t")
    content = value.get("c")
    if node_type == "Str" and isinstance(content, str):
        return content
    if node_type == "Math":
        return "$"
    if node_type == "Span" and isinstance(content, list) and len(content) == 2:
        content = content[1]
    return content if isinstance(content, list) else []


def _first_inline_text(value: PandocJson) -> str:
    """The text of the first `Str` or math in `value`."""
    children = _inline_children(value)
    if isinstance(children, str):
        return children
    return next((text for child in children if (text := _first_inline_text(child))), "")


def _last_inline_text(value: PandocJson) -> str:
    """The text of the last `Str` or math in `value`."""
    children = _inline_children(value)
    if isinstance(children, str):
        return children
    return next(
        (text for child in reversed(children) if (text := _last_inline_text(child))), ""
    )


def join_sourced_hyphen_breaks(source: str, pandoc_exe: str) -> tuple[str, int]:
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
                        before = _last_inline_text(value[index - 1])
                        after = _first_inline_text(value[index + 1])
                        first_word = after.split()[0] if after.split() else ""
                        joins = (
                            before.endswith("-")
                            and bool(after)
                            and first_word.strip(".,;:!?").lower()
                            not in SUSPENSION_WORDS
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
                                starts[position.end.line - 1] + position.end.column - 1
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
    return result, len(edits)


def set_sourced_list_spacing(source: str, pandoc_exe: str, spacing: ListSpacing) -> str:
    """Change gaps between items identified by Pandoc list nodes to `spacing`."""
    if spacing is ListSpacing.preserve:
        return source
    loose = spacing is ListSpacing.loose
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
        kind = located.node.get("t")
        if kind not in {"BulletList", "OrderedList"}:
            continue
        quoted = "BlockQuote" in located.ancestors
        noted = "Note" in located.ancestors
        nested = any(
            ancestor in {"BulletList", "OrderedList"} for ancestor in located.ancestors
        )
        if loose and nested:
            line_index = located.source_range.start.line - 1
            if 0 < line_index < len(lines) and lines[line_index - 1].strip(
                " \t\r\n>" if quoted else " \t\r\n"
            ):
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
            # An item whose text starts on the line after its marker has its
            # marker on an earlier line; its gap is left as written.
            if not lines[line_index][: position.start.column - 1].strip(" \t>"):
                continue
            blank = "\n"
            if quoted:
                marker_prefix = lines[line_index][: position.start.column - 1]
                quote_end = marker_prefix.rfind(">")
                if quote_end < 0:
                    continue
                blank = marker_prefix[: quote_end + 1] + "\n"
            elif noted:
                marker_prefix = lines[line_index][: position.start.column - 1]
                blank = (
                    marker_prefix[
                        : len(marker_prefix) - len(marker_prefix.lstrip(" \t"))
                    ]
                    + "\n"
                )
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
    return result


def normalize_sourced_marker_spacing(source: str, pandoc_exe: str) -> str:
    """
    Write a top-level list marker at the line start with one space after it,
    moving the item's other lines left by the same amount.

    Five or more spaces after a marker start indented code, so only two to four
    are closed up.
    """
    lines = source.splitlines(keepends=True)
    starts = list(accumulate(map(len, lines), initial=0))
    edits: list[SourceEdit] = []
    nodes = located_source_nodes(source, pandoc_exe)
    for located in nodes:
        if located.node.get("t") not in {"BulletList", "OrderedList"}:
            continue
        if not set(located.ancestors) <= {"Div"}:
            continue
        first = located.source_range.start.line
        last = min(located.source_range.end.line - 1, len(lines))
        # (line, indentation, offset of the spaces after the marker, surplus
        # spaces, content column)
        items: list[tuple[int, int, int, int, int]] = []
        for block in nodes:
            line = block.source_range.start.line
            # An item's blocks sit in the list's position wrapper and the list.
            item_ancestors = (*located.ancestors, "Div", located.node.get("t"))
            if not (first <= line <= last) or block.ancestors != item_ancestors:
                continue
            prefix = lines[line - 1][: block.source_range.start.column - 1]
            marker = _MARKER_SPACING.fullmatch(prefix)
            if marker is not None:
                items.append(
                    (
                        line,
                        len(marker.group(1)),
                        marker.start(3),
                        len(marker.group(3)) - 1,
                        len(prefix),
                    )
                )
        bounds = [item[0] for item in items] + [last + 1]
        for (line, indent, spaces, surplus, column), end in zip(
            items, bounds[1:], strict=True
        ):
            if indent == 0 and surplus == 0:
                continue
            offset = starts[line - 1] + spaces + 1
            edits.append(SourceEdit(offset, offset + surplus, ""))
            edits.append(SourceEdit(starts[line - 1], starts[line - 1] + indent, ""))
            for index in range(line, end - 1):
                if lines[index].startswith(" " * column):
                    edits.append(
                        SourceEdit(starts[index], starts[index] + indent + surplus, "")
                    )
    result = source
    for edit in sorted(set(edits), key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def normalize_sourced_list_indentation(source: str, pandoc_exe: str) -> str:
    """
    Indent a nested list's markers to its parent item's content column.

    Pandoc reports a nested list's start column as that content column. Each
    list moves its lines left by its surplus indentation, and a line inside
    several nested lists moves by their sum.
    """
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    shifts: dict[int, int] = {}
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") not in {"BulletList", "OrderedList"}:
            continue
        if any(ancestor in {"BlockQuote", "Note"} for ancestor in located.ancestors):
            continue
        if not any(
            ancestor in {"BulletList", "OrderedList"} for ancestor in located.ancestors
        ):
            continue
        first = located.source_range.start.line - 1
        last = min(located.source_range.end.line - 1, len(lines))
        if not (0 <= first < len(lines)):
            continue
        indent = len(lines[first]) - len(lines[first].lstrip(" "))
        delta = indent - (located.source_range.start.column - 1)
        if delta <= 0:
            continue
        for index in range(first, last):
            shifts[index] = shifts.get(index, 0) + delta
    edits: list[SourceEdit] = []
    for index, delta in shifts.items():
        if lines[index].startswith(" " * delta):
            edits.append(SourceEdit(starts[index], starts[index] + delta, ""))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def normalize_sourced_indented_code(source: str, pandoc_exe: str) -> str:
    """Write Pandoc indented code blocks with safe backtick fences."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
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
    return result


def normalize_sourced_blank_gaps(source: str, pandoc_exe: str) -> str:
    """Keep one empty separator line between authored blocks."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    protected: set[int] = set()
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") not in {"CodeBlock", "RawBlock", "Math"}:
            continue
        first = located.source_range.start.line - 1
        last = min(located.source_range.end.line - 1, len(lines))
        while last > first and not lines[last - 1].strip():
            last -= 1
        protected.update(range(first, last))
    edits: list[SourceEdit] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() or index in protected:
            index += 1
            continue
        start = index
        while (
            index < len(lines) and not lines[index].strip() and index not in protected
        ):
            index += 1
        if index - start > 1:
            edits.append(SourceEdit(starts[start], starts[index], "\n"))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


_DELIMITER_CELLS = {
    "AlignDefault": "---",
    "AlignLeft": ":---",
    "AlignRight": "---:",
    "AlignCenter": ":---:",
}


@dataclass(frozen=True)
class _PipeTable:
    """
    A Pandoc pipe table that starts a line outside any container but a div.

    `rows` are its authored lines, the header first and the delimiter row second.
    `cells` holds, for each row, the 0-based offsets in that line of the raw text
    of each cell Pandoc reads there: none for the delimiter row and none for a
    header Pandoc drops because all of its cells are empty. `alignments` are the
    delimiter cells of Pandoc's column alignments, and `wide` says whether Pandoc
    gives the columns relative widths.
    """

    first: int
    rows: list[str]
    cells: list[list[tuple[int, int]]]
    alignments: list[str]
    wide: bool


def _json_list(value: PandocJson) -> list[PandocJson]:
    assert isinstance(value, list)
    return value


def _json_tag(value: PandocJson) -> str:
    assert isinstance(value, dict)
    return cast(str, value["t"])


def _cell_ranges(row: PandocJson, lines: list[str]) -> list[tuple[int, int]]:
    """
    The 0-based offsets in its line of the raw text of each cell of `row`.

    pandoc-flowmark gives each pipe table cell the `data-pos` range of its raw text
    between the pipes, in Pandoc's tab-expanded columns.
    """
    ranges: list[tuple[int, int]] = []
    for cell in _json_list(_json_list(row)[1]):
        attributes = _json_list(_json_list(_json_list(cell)[0])[2])
        raw = next(
            cast(str, pair[1])
            for pair in map(_json_list, attributes)
            if pair[0] == "data-pos"
        )
        start, end = (
            character_point(SourcePoint(*map(int, point.split(":"))), lines)
            for point in raw.split("-")
        )
        ranges.append((start.column - 1, end.column - 1))
    return ranges


def _pipe_tables(source: str, pandoc_exe: str) -> list[_PipeTable]:
    """Each Pandoc pipe table that starts a line outside any container but a div."""
    lines = source.splitlines(keepends=True)
    texts = source.split("\n")
    tables: list[_PipeTable] = []
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") != "Table" or not set(located.ancestors) <= {"Div"}:
            continue
        first = located.source_range.start.line
        if located.source_range.start.column != 1 or first + 1 > len(lines):
            continue
        if not _PIPE_DELIMITER_ROW.fullmatch(lines[first].strip()):
            continue
        last = min(located.source_range.end.line - 1, len(lines))
        while last > first and not lines[last - 1].strip():
            last -= 1
        rows = [lines[index].rstrip("\r\n") for index in range(first - 1, last)]
        content = _json_list(located.node["c"])
        # Pandoc drops a header whose cells are all empty.
        head = [
            _cell_ranges(row, texts) for row in _json_list(_json_list(content[3])[1])
        ]
        body = [
            _cell_ranges(row, texts)
            for part in _json_list(content[4])
            for row in _json_list(_json_list(part)[3])
        ]
        colspecs = list(map(_json_list, _json_list(content[2])))
        tables.append(
            _PipeTable(
                first,
                rows,
                [head[0] if head else [], [], *body],
                [_DELIMITER_CELLS[_json_tag(spec[0])] for spec in colspecs],
                any(_json_tag(spec[1]) == "ColWidth" for spec in colspecs),
            )
        )
    return tables


def _dropped_text(row: str, cells: list[tuple[int, int]]) -> str:
    """The text of `row` outside every cell, without its pipes and spaces."""
    outside = row
    for start, end in reversed(cells):
        outside = outside[:start] + outside[end:]
    return "".join(outside.replace("|", " ").split())


def dropped_pipe_table_cells(source: str, pandoc_exe: str) -> list[Finding]:
    """
    Each pipe table row with text in no cell Pandoc reads.

    Pandoc drops the cells past the delimiter row's column count, and its reading
    is the same whatever is written there, so the meaning check cannot protect
    them.
    """
    findings: list[Finding] = []
    for table in _pipe_tables(source, pandoc_exe):
        for offset, (row, cells) in enumerate(zip(table.rows, table.cells)):
            if offset == 1 or not (dropped := _dropped_text(row, cells)):
                continue
            findings.append(
                Finding(
                    table.first + offset,
                    f"this row has text past the table's {len(table.alignments)} "
                    + f"columns, and Pandoc drops it: `{dropped}`. A `|` inside a "
                    + "cell ends the cell: write it as `\\|`",
                )
            )
    return findings


def _normalized_pipe_table(table: _PipeTable) -> list[str]:
    """
    The rows of `table` as `| a | b |`, with a `| --- |` delimiter row.

    The cells are the ones Pandoc reads; a header Pandoc drops has only empty
    cells. Past Pandoc's column limit the delimiter row's dash counts set the
    relative column widths (Pandoc manual, "pipe_tables"), so it stays as written.
    """
    rows: list[str] = []
    for offset, (row, cells) in enumerate(zip(table.rows, table.cells)):
        if offset == 1:
            rows.append(
                row if table.wide else "| " + " | ".join(table.alignments) + " |"
            )
            continue
        texts = [row[start:end].strip() for start, end in cells]
        if offset == 0 and not cells:
            texts = [""] * len(table.alignments)
        rows.append("| " + " | ".join(texts) + " |")
    return rows


def normalize_sourced_pipe_tables(source: str, pandoc_exe: str) -> str:
    """
    Write each row of a Pandoc pipe table as `| a | b |`, with a `| --- |` rule.

    A table whose rewrite Pandoc gives other column widths stays as written.
    """
    lines = source.splitlines(keepends=True)
    starts = list(accumulate((len(line) for line in lines), initial=0))
    tables = _pipe_tables(source, pandoc_exe)
    edits = [
        [
            SourceEdit(
                starts[table.first - 1 + offset],
                starts[table.first - 1 + offset] + len(raw),
                row,
            )
            for offset, (raw, row) in enumerate(
                zip(table.rows, _normalized_pipe_table(table), strict=True)
            )
            if row != raw
        ]
        for table in tables
    ]
    rewritten = _apply_edits(source, [edit for table in edits for edit in table])
    widths = [table.wide for table in _pipe_tables(rewritten, pandoc_exe)]
    kept = [
        edit
        for table, table_edits, wide in zip(tables, edits, widths, strict=True)
        if table.wide == wide
        for edit in table_edits
    ]
    return _apply_edits(source, kept)


def _apply_edits(source: str, edits: list[SourceEdit]) -> str:
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def normalize_sourced_rules(source: str, pandoc_exe: str) -> str:
    """Write each top-level horizontal rule as `* * *`."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") != "HorizontalRule":
            continue
        if not set(located.ancestors) <= {"Div"}:
            continue
        index = located.source_range.start.line - 1
        raw = lines[index].rstrip("\r\n")
        if raw != "* * *":
            edits.append(SourceEdit(starts[index], starts[index] + len(raw), "* * *"))
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def separate_sourced_note_definitions(source: str, pandoc_exe: str) -> str:
    """Write a blank line before a footnote definition that follows a text line."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    definition_lines = {
        located.source_range.start.line
        for located in located_source_nodes(source, pandoc_exe)
        if located.ancestors and located.ancestors[-1] == "Note"
    }
    blank_before = [
        line_number
        for line_number in definition_lines
        if 1 < line_number <= len(lines)
        and lines[line_number - 1].startswith("[^")
        and lines[line_number - 2].strip()
        and not _LINK_DEFINITION.match(lines[line_number - 2])
    ]
    result = source
    for line_number in sorted(blank_before, reverse=True):
        offset = starts[line_number - 1]
        result = result[:offset] + "\n" + result[offset:]
    return result


def normalize_sourced_quote_blank_lines(source: str, pandoc_exe: str) -> str:
    """Write a space after the marker of an empty blockquote line."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits: set[SourceEdit] = set()
    nodes = located_source_nodes(source, pandoc_exe)
    # Blank lines inside a quoted list or code block keep the bare marker.
    kept_lines = {
        index
        for node in nodes
        if node.node.get("t") in {"BulletList", "OrderedList", "CodeBlock"}
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
            if index not in kept_lines and re.fullmatch(r"[ >]*>", raw):
                edits.add(
                    SourceEdit(starts[index] + len(raw), starts[index] + len(raw), " ")
                )
    result = source
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def apply_sourced_smart_quotes(source: str, pandoc_exe: str) -> str:
    """
    Apply prose quote style only at inline text owned by Pandoc.

    A quotation inside another is matched only once the outer quotes are curled,
    so the style is applied until it changes nothing.
    """
    result = source
    while True:
        styled = _apply_smart_quotes_once(result, pandoc_exe)
        if styled == result:
            break
        result = styled
    return result


def _apply_smart_quotes_once(source: str, pandoc_exe: str) -> str:
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    def offsets(located: LocatedNode) -> tuple[int, int] | None:
        begin = located.source_range.start
        finish = located.source_range.end
        if begin.line > len(lines) or finish.line > len(lines) + 1:
            return None
        start = starts[begin.line - 1] + begin.column - 1
        end = starts[finish.line - 1] + finish.column - 1
        return (start, end) if 0 <= start < end <= len(source) else None

    nodes = located_source_nodes(source, pandoc_exe)
    # Quotes pair within one block of text, so each paragraph, plain block,
    # heading, and table row is styled on its own; a stray quote elsewhere
    # cannot pair with them.
    runs: list[tuple[int, int]] = []
    for located in nodes:
        kind = located.node.get("t")
        span = offsets(located)
        if span is None:
            continue
        if kind in {"Para", "Plain", "Header"}:
            runs.append(span)
        elif kind == "Table":
            first = located.source_range.start.line - 1
            last = min(located.source_range.end.line, len(lines) + 1) - 1
            runs.extend(
                (starts[index], starts[index + 1]) for index in range(first, last)
            )
    styled = list(source)
    for start, end in runs:
        text = smart_quotes(source[start:end])
        if len(text) != end - start:
            raise ValueError("Smart quote conversion changed source length")
        styled[start:end] = text

    eligible: set[int] = set()
    for located in nodes:
        kind = located.node.get("t")
        if kind not in {"Str", "Quoted"}:
            continue
        span = offsets(located)
        if span is None:
            continue
        start, end = span
        if kind == "Str":
            eligible.update(range(start, end))
        elif source[start] in "'\"" and source[end - 1] == source[start]:
            eligible.update((start, end - 1))

    result = list(source)
    for index in eligible:
        if source[index] in "'\"" and styled[index] in "‘’“”":
            result[index] = styled[index]
    return "".join(result)


def apply_sourced_ellipses(source: str, pandoc_exe: str) -> str:
    """Style ellipses only where Pandoc decoded literal prose to an ellipsis."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") != "Str" or "…" not in str(located.node.get("c", "")):
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
        # What follows the ellipsis decides its spacing, and wrapping moves line
        # breaks, so a break inside the paragraph is looked across: the window
        # ends at the next line's first character, or at the paragraph's end.
        if right < len(source) and source[right] == "\n":
            following = right + 1
            while following < len(source) and source[following] in " \t>":
                following += 1
            if following < len(source) and source[following] != "\n":
                right = following + 1
        elif right < len(source):
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
    return result


def set_sourced_heading_spacing(source: str, pandoc_exe: str) -> str:
    """Separate Pandoc headings from the next authored block."""
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))

    edits: list[SourceEdit] = []
    for located in located_source_nodes(source, pandoc_exe):
        if located.node.get("t") != "Header":
            continue
        next_line = located.source_range.end.line
        if not (1 < next_line <= len(lines)):
            continue
        previous = lines[next_line - 2].rstrip("\r\n")
        if not previous.strip(" \t>"):
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
    return result


def set_sourced_tag_block_spacing(source: str, pandoc_exe: str) -> str:
    """
    Separate a template tag line from a list or table directly beside it.

    Pandoc's `flowmark_tags` reader already reads the tag line as its own block.
    The blank line keeps CommonMark readers such as Markdoc from reading the tag
    as lazy continuation text of the list or table.
    """
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    nodes = [
        node
        for node in located_source_nodes(source, pandoc_exe)
        if set(node.ancestors) <= {"Div"}
    ]
    tags = [
        node
        for node in nodes
        if node.node.get("t") == "RawBlock"
        and cast(list[PandocJson], node.node["c"])[0] in {"flowmark-tag", "html"}
        and is_tag_only_line(cast(str, cast(list[PandocJson], node.node["c"])[1]))
    ]
    blocks = [
        node
        for node in nodes
        if node.node.get("t") in {"BulletList", "OrderedList", "Table"}
    ]

    # A range ends where the next block starts, so it can span trailing blank
    # lines; adjacency is judged from the node's last non-blank line.
    def last_content_line(node: LocatedNode) -> int:
        last = min(node.source_range.end.line, len(lines) + 1) - 1
        while last > node.source_range.start.line and not lines[last - 1].strip():
            last -= 1
        return last

    blank_before: set[int] = set()
    for tag in tags:
        for block in blocks:
            if block.ancestors != tag.ancestors:
                continue
            if block.source_range.start.line == last_content_line(tag) + 1:
                blank_before.add(block.source_range.start.line)
            if last_content_line(block) == tag.source_range.start.line - 1:
                blank_before.add(tag.source_range.start.line)
    result = source
    for line_number in sorted(blank_before, reverse=True):
        offset = starts[line_number - 1]
        result = result[:offset] + "\n" + result[offset:]
    return result


def _interrupting_list_lines(
    lines: list[str], paragraphs: list[LocatedNode], pandoc_exe: str
) -> set[int]:
    """
    The 1-based lines after the first of `paragraphs` that open a list when read
    on their own: a bullet list, or an ordered list starting at 1, the lists
    CommonMark lets interrupt a paragraph.
    """
    candidates = [
        line_number
        for node in paragraphs
        for line_number in range(
            node.source_range.start.line + 1,
            min(node.source_range.end.line, len(lines) + 1),
        )
        if lines[line_number - 1].strip()
    ]
    if not candidates:
        return set()
    # Probe every candidate line in one parse, each on its own between unindented
    # paragraphs that close any list the line before opened.
    probe = "".join(
        f"x\n\n{lines[line_number - 1].rstrip()}\n\n" for line_number in candidates
    )
    list_starts: set[int] = set()
    for located in located_source_nodes(probe, pandoc_exe):
        if located.ancestors:
            continue
        kind = located.node.get("t")
        content = located.node.get("c")
        opens_list = kind == "BulletList" or (
            kind == "OrderedList"
            and isinstance(content, list)
            and cast(list[PandocJson], content[0])[0] == 1
        )
        if opens_list:
            probe_index = (located.source_range.start.line - 3) // 4
            list_starts.add(candidates[probe_index])
    return list_starts


def separate_sourced_lazy_lists(source: str, pandoc_exe: str) -> str:
    """
    Set off a list that the author wrote directly under a paragraph line.

    Pandoc's Markdown reader does not let a list interrupt a paragraph, so it reads
    such bullets as paragraph text; CommonMark reads a list, as the author drew
    it. A blank line before the first bullet gives Pandoc the same list. Following
    CommonMark's interruption rule, only a bullet list or an ordered list starting
    at 1 counts, so a wrapped line such as `2019. The year` stays prose.
    """
    lines = source.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    paragraphs = [
        node
        for node in located_source_nodes(source, pandoc_exe)
        if node.node.get("t") == "Para" and set(node.ancestors) <= {"Div"}
    ]
    list_starts = _interrupting_list_lines(lines, paragraphs, pandoc_exe)
    if not list_starts:
        return source
    blank_before = {
        min(
            line
            for line in list_starts
            if node.source_range.start.line < line < node.source_range.end.line
        )
        for node in paragraphs
        if any(
            node.source_range.start.line < line < node.source_range.end.line
            for line in list_starts
        )
    }

    def separated(lines_before: set[int]) -> str:
        result = source
        for line_number in sorted(lines_before, reverse=True):
            offset = starts[line_number - 1]
            result = result[:offset] + "\n" + result[offset:]
        return result

    # The new list must read as the paragraph text it replaces, which the
    # lazy_list normalization checks; a list holding a fenced code block does
    # not, and its paragraph is left as written. Pandoc decides, first for all
    # lists at once and, if that fails, one by one.
    candidate = separated(blank_before)
    if candidate == source:
        return source
    try:
        check_meaning_preserved(source, candidate)
    except MeaningChangedError:
        accepted: set[int] = set()
        for line_number in sorted(blank_before):
            try:
                check_meaning_preserved(source, separated({line_number}))
            except MeaningChangedError:
                continue
            accepted.add(line_number)
        candidate = separated(accepted)
    return candidate


def format_sourced_markdown(
    source: str,
    pandoc_exe: str,
    options: FormatOptions,
    line_wrapper: LineWrapper | None = None,
) -> tuple[str, int]:
    """
    Run the formatting edits `options` selects through one Pandoc source map,
    and return the result and the number of line breaks closed after a hyphen.
    `line_wrapper`, when given, wraps every paragraph in place of the wrapping
    `options.wrap` selects. Nothing here checks that the result reads as the
    source does; the caller decides that.
    """
    result = expand_sourced_leading_tabs(source, pandoc_exe)
    result = normalize_sourced_spelling(result, pandoc_exe)
    result = set_sourced_tag_block_spacing(result, pandoc_exe)
    result = separate_sourced_lazy_lists(result, pandoc_exe)
    result = separate_sourced_note_definitions(result, pandoc_exe)
    # Indentation settles before wrapping, which measures lines with it.
    result = normalize_sourced_marker_spacing(result, pandoc_exe)
    result = normalize_sourced_list_indentation(result, pandoc_exe)
    result = normalize_sourced_html_block_layout(result, pandoc_exe)
    joined = 0
    if Pass.cleanups in options.passes:
        result = unbold_sourced_headings(result, pandoc_exe)
        result, joined = join_sourced_hyphen_breaks(result, pandoc_exe)
    if Pass.smartquotes in options.passes:
        result = apply_sourced_smart_quotes(result, pandoc_exe)
    if Pass.ellipses in options.passes:
        result = apply_sourced_ellipses(result, pandoc_exe)
    match options.wrap:
        case Plain():
            raise ValueError("Plain wrapping applies to plain text, not Markdown")
        case _ if line_wrapper is not None:
            wrappers = ParagraphWrappers(line_wrapper, line_wrapper, line_wrapper)
            result = wrap_plain_paragraphs(result, pandoc_exe, wrappers)
        case Width(width) if width <= 0:
            pass
        case Width() | Semantic():
            wrappers = paragraph_wrappers(options.wrap)
            result = wrap_plain_paragraphs(result, pandoc_exe, wrappers)
    result = set_sourced_list_spacing(result, pandoc_exe, options.list_spacing)
    result = normalize_sourced_indented_code(result, pandoc_exe)
    result = normalize_sourced_pipe_tables(result, pandoc_exe)
    result = normalize_sourced_rules(result, pandoc_exe)
    result = normalize_sourced_blank_gaps(result, pandoc_exe)
    result = set_sourced_heading_spacing(result, pandoc_exe)
    result = normalize_sourced_quote_blank_lines(result, pandoc_exe)
    return result, joined

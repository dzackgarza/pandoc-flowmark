"""
Tag handling for Jinja/Markdoc tags and HTML comments.

This module provides detection and handling of template tags used by systems like
Markdoc, Markform, Jinja, Nunjucks, and WordPress Gutenberg.

The main concerns are:
1. Detecting tag boundaries to preserve newlines around them
2. Normalizing and denormalizing adjacent tags for proper tokenization
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from enum import Enum
from typing import NamedTuple

from flowmark.linewrapping.atomic_patterns import (
    PAIRED_HTML_COMMENT,
    PAIRED_JINJA_COMMENT,
    PAIRED_JINJA_TAG,
    PAIRED_JINJA_VAR,
    SINGLE_HTML_COMMENT,
    SINGLE_JINJA_COMMENT,
    SINGLE_JINJA_TAG,
    SINGLE_JINJA_VAR,
)
from flowmark.linewrapping.protocols import LineWrapper
from flowmark.pandoc_reader import (
    PandocJson,
    located_nodes,
    pandoc_executable,
    read_source_ast,
)

# Pattern to match complete template tags (for protecting content inside tags).
# Uses the single tag patterns from atomic_patterns.
TEMPLATE_TAG_PATTERN: re.Pattern[str] = re.compile(
    "|".join(
        [
            SINGLE_JINJA_TAG.pattern,
            SINGLE_JINJA_COMMENT.pattern,
            SINGLE_JINJA_VAR.pattern,
            SINGLE_HTML_COMMENT.pattern,
        ]
    ),
    re.DOTALL,
)

# Pattern to match paired tags like {% tag %}{% /tag %} that should stay together.
# Uses paired tag patterns from atomic_patterns.
PAIRED_TAGS_PATTERN: re.Pattern[str] = re.compile(
    "|".join(
        [
            PAIRED_JINJA_TAG.pattern,
            PAIRED_JINJA_COMMENT.pattern,
            PAIRED_JINJA_VAR.pattern,
            PAIRED_HTML_COMMENT.pattern,
        ]
    ),
    re.DOTALL,
)


# Pattern to detect adjacent tags (closing tag immediately followed by opening tag)
# This handles cases like %}{% or --><!-- where there's no space between
_adjacent_tags_re: re.Pattern[str] = re.compile(
    rf"({SINGLE_JINJA_TAG.close_re})({SINGLE_JINJA_TAG.open_re})|"
    rf"({SINGLE_JINJA_COMMENT.close_re})({SINGLE_JINJA_COMMENT.open_re})|"
    rf"({SINGLE_JINJA_VAR.close_re})({SINGLE_JINJA_VAR.open_re})|"
    rf"({SINGLE_HTML_COMMENT.close_re})({SINGLE_HTML_COMMENT.open_re})"
)

# Pattern to remove spaces between adjacent tags that were added during word splitting
_denormalize_tags_re: re.Pattern[str] = re.compile(
    rf"({SINGLE_JINJA_TAG.close_re}) ({SINGLE_JINJA_TAG.open_re})|"
    rf"({SINGLE_JINJA_COMMENT.close_re}) ({SINGLE_JINJA_COMMENT.open_re})|"
    rf"({SINGLE_JINJA_VAR.close_re}) ({SINGLE_JINJA_VAR.open_re})|"
    rf"({SINGLE_HTML_COMMENT.close_re}) ({SINGLE_HTML_COMMENT.open_re})"
)


def normalize_adjacent_tags(text: str) -> str:
    """
    Add a space between adjacent tags so they become separate tokens.
    For example: %}{% becomes %} {%
    """

    def add_space(match: re.Match[str]) -> str:
        groups = match.groups()
        for i in range(0, len(groups), 2):
            if groups[i] is not None:
                return groups[i] + " " + groups[i + 1]
        return match.group(0)

    return _adjacent_tags_re.sub(add_space, text)


def denormalize_adjacent_tags(text: str) -> str:
    """
    Remove spaces between adjacent tags that were added during word splitting.
    This restores original adjacency for paired tags like `{% field %}{% /field %}`.
    """

    def remove_space(match: re.Match[str]) -> str:
        groups = match.groups()
        for i in range(0, len(groups), 2):
            if groups[i] is not None:
                return groups[i] + groups[i + 1]
        return match.group(0)

    return _denormalize_tags_re.sub(remove_space, text)


def is_tag_only_line(line: str) -> bool:
    """
    Check if a line contains only a tag (opening or closing), not inline tags in content.

    A tag-only line starts with a tag delimiter and ends with a tag delimiter,
    with no substantial non-tag content. This distinguishes:
    - `{% field %}` (tag-only line)
    - `- [ ] Item {% #id %}` (content with inline tag - NOT tag-only)
    - `  <!-- #kg-32zz -->` (indented continuation - NOT tag-only)

    Indented lines are never considered "tag-only" because they are continuations
    of previous content (like list items), not standalone tag blocks.
    """
    # Indented lines are continuations, not standalone tag blocks
    if line and line[0].isspace():
        return False

    stripped = line.strip()
    if not stripped:
        return False

    # Check if it starts with a tag
    starts_tag = (
        stripped.startswith(SINGLE_JINJA_TAG.open_delim)
        or stripped.startswith(SINGLE_JINJA_COMMENT.open_delim)
        or stripped.startswith(SINGLE_JINJA_VAR.open_delim)
        or stripped.startswith(SINGLE_HTML_COMMENT.open_delim)
    )

    # Check if it ends with a tag
    ends_tag = (
        stripped.endswith(SINGLE_JINJA_TAG.close_delim)
        or stripped.endswith(SINGLE_JINJA_COMMENT.close_delim)
        or stripped.endswith(SINGLE_JINJA_VAR.close_delim)
        or stripped.endswith(SINGLE_HTML_COMMENT.close_delim)
    )

    return starts_tag and ends_tag


def _blocks(lines: Sequence[str]) -> list[dict[str, PandocJson]]:
    """Top-level blocks from Flowmark's Pandoc reader."""
    source = "\n".join(lines) + "\n"
    return [
        node.node
        for node in located_nodes(read_source_ast(source, pandoc_executable()))
        if not node.ancestors
    ]


def _is_list_item_line(line: str) -> bool:
    """Whether the parser reads `line`, on its own, as the start of a list."""
    blocks = _blocks([line])
    return bool(blocks and blocks[0].get("t") in {"BulletList", "OrderedList"})


def _table_length(lines: Sequence[str]) -> int:
    """
    How many of `lines`, from the first, the parser reads as one pipe table, or 0
    if they do not open one.
    """
    # The header and delimiter rows alone decide whether a table opens here, so the
    # whole run is parsed only when one does. Both rows of a pipe table contain a
    # pipe (Pandoc manual, "pipe_tables").
    if len(lines) < 2 or "|" not in lines[0] or "|" not in lines[1]:
        return 0
    first = _blocks(lines[:2])
    if not first or first[0].get("t") != "Table":
        return 0
    blocks = _blocks(lines)
    if not blocks or blocks[0].get("t") != "Table":
        return 0
    content = blocks[0].get("c")
    if not isinstance(content, list) or len(content) < 5:
        return 0
    bodies = content[4]
    if not isinstance(bodies, list):
        return 0
    rows = 0
    for body in bodies:
        if isinstance(body, list) and len(body) == 4 and isinstance(body[3], list):
            rows += len(body[3])
    return rows + 2


def line_ends_with_tag(line: str) -> bool:
    """Check if a line ends with a Jinja/Markdoc tag or HTML comment."""
    stripped = line.rstrip()
    if not stripped:
        return False
    # Check for Jinja-style tags
    if (
        stripped.endswith(SINGLE_JINJA_TAG.close_delim)
        or stripped.endswith(SINGLE_JINJA_COMMENT.close_delim)
        or stripped.endswith(SINGLE_JINJA_VAR.close_delim)
    ):
        return True
    # Check for HTML comments
    if stripped.endswith(SINGLE_HTML_COMMENT.close_delim):
        return True
    return False


def line_starts_with_tag(line: str) -> bool:
    """Check if a line starts with a Jinja/Markdoc tag or HTML comment."""
    stripped = line.lstrip()
    if not stripped:
        return False
    # Check for Jinja-style tags
    if (
        stripped.startswith(SINGLE_JINJA_TAG.open_delim)
        or stripped.startswith(SINGLE_JINJA_COMMENT.open_delim)
        or stripped.startswith(SINGLE_JINJA_VAR.open_delim)
    ):
        return True
    # Check for HTML comments
    if stripped.startswith(SINGLE_HTML_COMMENT.open_delim):
        return True
    return False


def _is_unindented_tag_line(line: str) -> bool:
    """
    Check if a line is an unindented line that starts with a tag.

    This distinguishes between:
    - `{% field %}` at the start of a line (returns True)
    - `  <!-- #tag -->` which is indented continuation (returns False)

    Indented tag lines are typically continuations of previous content
    (like list items) and should not trigger segment breaks or blank line
    insertion in tag handling.
    """
    if not line:
        return False
    # Check if the line has leading whitespace
    if line[0].isspace():
        return False
    return line_starts_with_tag(line)


class _SegmentKind(Enum):
    text = "text"
    list_item = "list_item"
    table = "table"


class _Segment(NamedTuple):
    """Consecutive lines of a paragraph's text that are wrapped, or kept, together."""

    lines: list[str]
    kind: _SegmentKind

    @property
    def is_block(self) -> bool:
        return self.kind is not _SegmentKind.text


def _no_list_item(_line: str) -> bool:
    return False


def _segments(
    lines: Sequence[str], is_list_item: Callable[[str], bool]
) -> list[_Segment]:
    """
    Split a paragraph's lines where a tag or a block begins or ends.

    A run of lines the parser reads as a pipe table is always a segment of its own:
    a row must never be wrapped. So is each line `is_list_item` accepts.
    """
    segments: list[_Segment] = []
    i = 0
    while i < len(lines):
        table_length = _table_length(lines[i:])
        if table_length:
            segments.append(
                _Segment(list(lines[i : i + table_length]), _SegmentKind.table)
            )
            i += table_length
            continue
        line = lines[i]
        if is_list_item(line):
            segments.append(_Segment([line], _SegmentKind.list_item))
        elif (
            segments
            and segments[-1].kind is _SegmentKind.text
            and not line_ends_with_tag(lines[i - 1])
            # Only unindented tag lines are boundaries; indented ones are
            # continuations (e.g., of a list item).
            and not _is_unindented_tag_line(line)
        ):
            segments[-1].lines.append(line)
        else:
            segments.append(_Segment([line], _SegmentKind.text))
        i += 1
    return segments


def add_tag_newline_handling(
    base_wrapper: LineWrapper,
) -> LineWrapper:
    """
    Augments a LineWrapper to preserve newlines around Jinja/Markdoc tags
    and HTML comments.

    When a line ends with a tag or the next line starts with a tag,
    the newline between them is preserved rather than being normalized
    away during text wrapping.

    This enables compatibility with Markdoc, Markform, and similar systems
    that use block-level tags like `{% field %}...{% /field %}`.

    Both atomic and wrap modes apply the multiline tag fix (workaround for
    Markdoc parser bug - see GitHub issue #17).

    This operates on paragraph text. A tag alone on its unindented line is a block
    of its own to Pandoc's `flowmark_tags` reader, so it never reaches a paragraph
    here; the lines kept apart are those where a tag shares a line with text.
    """

    def enhanced_wrapper(text: str, initial_indent: str, subsequent_indent: str) -> str:
        # If no newlines in input, just wrap and apply post-processing fixes.
        # The base_wrapper may produce multi-line output that needs fixing.
        if "\n" not in text:
            result = base_wrapper(text, initial_indent, subsequent_indent)
            # Fix multiline tags: ensure closing tag on own line when opening spans lines.
            # This applies in both atomic and wrap modes to work around Markdoc parser bug.
            result = fix_multiline_opening_tag_with_closing(result)
            return result

        lines = text.split("\n")

        # If only one line after split, same as above
        if len(lines) <= 1:
            result = base_wrapper(text, initial_indent, subsequent_indent)
            result = fix_multiline_opening_tag_with_closing(result)
            return result

        # Check if there are any tags in the text - only split off list items
        # when tags are present to avoid changing normal markdown behavior.
        has_tags = any(
            line_ends_with_tag(line) or line_starts_with_tag(line) for line in lines
        )

        segments = _segments(lines, _is_list_item_line if has_tags else _no_list_item)

        # A single text segment means no tag or block boundaries were found
        if len(segments) == 1 and segments[0].kind is _SegmentKind.text:
            result = base_wrapper(text, initial_indent, subsequent_indent)
            result = fix_multiline_opening_tag_with_closing(result)
            return result

        # Wrap each segment separately. A table's rows are kept as written, one
        # per line: wrapping would break a row, and the text of a paragraph is
        # never rewritten into a table's normalized form.
        wrapped_segments: list[str] = []
        for i, segment in enumerate(segments):
            cur_initial_indent = initial_indent if i == 0 else subsequent_indent
            if segment.kind is _SegmentKind.table:
                wrapped = "\n".join(
                    (cur_initial_indent if j == 0 else subsequent_indent) + line
                    for j, line in enumerate(segment.lines)
                )
            else:
                wrapped = base_wrapper(
                    "\n".join(segment.lines), cur_initial_indent, subsequent_indent
                )
            wrapped_segments.append(wrapped)

        # Rejoin segments, normalizing newlines around block content.
        # Between a tag and block content (list/table), and between block content
        # and a closing tag, ensure exactly one blank line to prevent CommonMark
        # lazy continuation.
        result_parts: list[str] = [wrapped_segments[0]]
        for prev, curr, wrapped in zip(segments, segments[1:], wrapped_segments[1:]):
            prev_is_tag = line_ends_with_tag(prev.lines[-1])
            # Only unindented tag lines count as a tag here; indented ones are
            # continuations. A closing tag counts indented or not: it is
            # dedented below.
            curr_is_tag = _is_unindented_tag_line(curr.lines[0])
            curr_is_closing_tag = _is_closing_tag(curr.lines[0])
            if (prev_is_tag and curr.is_block) or (
                prev.is_block and (curr_is_tag or curr_is_closing_tag)
            ):
                result_parts.append("")  # Empty string creates blank line when joined
            result_parts.append(wrapped)

        result = "\n".join(result_parts)

        # Post-process: closing tags take no indentation.
        # The Markdown parser may indent closing tags due to lazy continuation.
        result = _dedent_closing_tags(result)

        # Fix multi-line opening tags that have closing tags on the same line.
        # This works around a Markdoc parser bug (see GitHub issue #17).
        result = fix_multiline_opening_tag_with_closing(result)

        return result

    return enhanced_wrapper


def _is_closing_tag(line: str) -> bool:
    """Check if a line is a closing tag."""
    stripped = line.lstrip()
    return (
        stripped.startswith("{% /")
        or stripped.startswith("{# /")
        or stripped.startswith("{{ /")
        or stripped.startswith("<!-- /")
    )


def _dedent_closing_tags(text: str) -> str:
    """
    Strip indentation from closing tags, which the Markdown parser may have
    indented as list continuation.
    """
    return "\n".join(
        line.lstrip() if _is_closing_tag(line) else line for line in text.split("\n")
    )


# Pattern to detect closing delimiter of opening tag followed by a closing tag.
# This handles cases like:  %}{% /tag %}  or  --><!-- /tag -->
# where a multi-line opening tag ends and a closing tag follows on the same line.
# Uses named group "closing_tag" to capture the start of the closing tag.
_multiline_closing_pattern: re.Pattern[str] = re.compile(
    rf"{SINGLE_JINJA_TAG.close_re}\s*(?P<closing_tag>{SINGLE_JINJA_TAG.open_re}\s*/)|"
    rf"{SINGLE_JINJA_COMMENT.close_re}\s*(?P<closing_comment>{SINGLE_JINJA_COMMENT.open_re}\s*/)|"
    rf"{SINGLE_JINJA_VAR.close_re}\s*(?P<closing_var>{SINGLE_JINJA_VAR.open_re}\s*/)|"
    rf"{SINGLE_HTML_COMMENT.close_re}\s*(?P<closing_html>{SINGLE_HTML_COMMENT.open_re}\s*/)"
)


def fix_multiline_opening_tag_with_closing(text: str) -> str:
    """
    Ensure closing tags are on their own line when the opening tag spans multiple lines.

    This works around a Markdoc parser bug where multi-line opening tags with
    closing tags on the same line cause incorrect AST parsing.

    Problem pattern (triggers Markdoc bug):
        {% tag attr1=value1
        attr2=value2 %}{% /tag %}

    Fixed pattern:
        {% tag attr1=value1
        attr2=value2 %}
        {% /tag %}

    Single-line paired tags like `{% field %}{% /field %}` are NOT affected.
    Tags in the middle of prose like `Before {% field %}{% /field %} after` are
    also NOT affected because the line contains content before the tag.
    """
    # Only apply fix if there are multiple lines - single line input means
    # no multi-line tags to fix
    if "\n" not in text:
        return text

    lines = text.split("\n")
    result_lines: list[str] = []

    for i, line in enumerate(lines):
        # Skip the first line - it can't be a continuation of a multi-line tag
        if i == 0:
            result_lines.append(line)
            continue

        stripped = line.lstrip()

        # Only process lines that are continuations (don't start with a tag opener).
        # If a line starts with a tag opener, the tag began on that line, not a continuation.
        is_tag_start = (
            stripped.startswith(SINGLE_JINJA_TAG.open_delim)
            or stripped.startswith(SINGLE_JINJA_COMMENT.open_delim)
            or stripped.startswith(SINGLE_JINJA_VAR.open_delim)
            or stripped.startswith(SINGLE_HTML_COMMENT.open_delim)
        )

        if not is_tag_start:
            match = _multiline_closing_pattern.search(line)
            if match:
                # Find which named group matched and split at the closing tag
                for group_name in [
                    "closing_tag",
                    "closing_comment",
                    "closing_var",
                    "closing_html",
                ]:
                    if match.group(group_name) is not None:
                        split_pos = match.start(group_name)
                        before = line[:split_pos].rstrip()
                        closing = line[split_pos:].lstrip()
                        result_lines.append(before)
                        result_lines.append(closing)
                        break
                continue

        result_lines.append(line)

    return "\n".join(result_lines)

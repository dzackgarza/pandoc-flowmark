from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from flowmark.linewrapping.atomic_patterns import TEMPLATE_TAG_PATTERNS
from flowmark.linewrapping.protocols import LineWrapper
from flowmark.linewrapping.sentence_split_regex import split_sentences_with_spans
from flowmark.linewrapping.tag_handling import (
    add_tag_newline_handling,
    denormalize_adjacent_tags,
)
from flowmark.linewrapping.text_filling import DEFAULT_WRAP_WIDTH
from flowmark.linewrapping.text_wrapping import (
    DEFAULT_LEN_FUNCTION,
    keep_word,
    markdown_escape_word,
    normalize_whitespace,
    wrap_paragraph,
    wrap_paragraph_lines,
)

DEFAULT_MIN_LINE_LEN = 20
"""Default minimum line length for sentence breaking."""


def _escape_line_start(line: str, escape_word: Callable[[str], str]) -> str:
    """Apply `escape_word` to `line`'s first word, which starts a line."""
    first, space, rest = line.partition(" ")
    return escape_word(first) + space + rest


class SentenceSplitter(Protocol):
    """Takes a text string and returns a list of sentences."""

    def __call__(self, text: str) -> list[str]: ...


def split_sentences_no_min_length(text: str) -> list[str]:
    # A link, code span or math span arrives with no whitespace in it (the sourced
    # wrapper hides the whitespace in each atom Pandoc locates), so a "St." inside
    # link text is not a word end and cannot trip the end-of-sentence heuristic.
    # Tags have no parse node, so they are kept whole by pattern.
    return [span.text for span in split_sentences_with_spans(text, min_length=0, patterns=TEMPLATE_TAG_PATTERNS)]


_line_break_re = re.compile(r"\\\n|  \n")


def split_markdown_hard_breaks(text: str) -> list[str]:
    """
    Split text by explicit Markdown line breaks.
    """
    return _line_break_re.split(text)


def _add_markdown_hard_break_handling(base_wrapper: LineWrapper) -> LineWrapper:
    """
    Augments a LineWrapper to first split the text by Markdown hard breaks,
    wrap each segment using the base_wrapper, and then rejoin them with
    a normalized Markdown hard break (backslash-newline).
    """

    def enhanced_wrapper(text: str, initial_indent: str, subsequent_indent: str) -> str:
        segments = split_markdown_hard_breaks(text)

        # Handle empty input.
        if not segments:
            return ""
        # Handle single segment (no hard line breaks).
        if len(segments) == 1:
            return base_wrapper(text, initial_indent, subsequent_indent)

        wrapped_segments: list[str] = []

        for i, segment in enumerate(segments):
            is_first = i == 0
            is_last = i == len(segments) - 1

            cur_initial_indent = initial_indent if is_first else subsequent_indent
            wrapped_segment = base_wrapper(segment, cur_initial_indent, subsequent_indent)
            if is_last:
                wrapped_segments.append(wrapped_segment)
            else:
                wrapped_segments.append(wrapped_segment + "\\")

        return "\n".join(wrapped_segments)

    return enhanced_wrapper


def line_wrap_to_width(
    width: int = DEFAULT_WRAP_WIDTH,
    len_fn: Callable[[str], int] = DEFAULT_LEN_FUNCTION,
    escape_word: Callable[[str], str] = keep_word,
) -> LineWrapper:
    """
    Wrap lines of text to a given width. `escape_word` rewrites the first word of
    each wrapped line after the first.
    """

    def line_wrapper(text: str, initial_indent: str, subsequent_indent: str) -> str:
        return wrap_paragraph(
            normalize_whitespace(text),
            width=width,
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            len_fn=len_fn,
            escape_word=escape_word,
        )

    return line_wrapper


def markdown_line_wrap_to_width(
    width: int = DEFAULT_WRAP_WIDTH,
    len_fn: Callable[[str], int] = DEFAULT_LEN_FUNCTION,
) -> LineWrapper:
    """
    Wrap lines of Markdown text to a given width: escape a wrapped line's first word
    when Markdown reads it as syntax, keep tag-only lines, and keep hard breaks.
    """
    # Apply tag newline handling first, then hard break handling
    # Order matters: tag handling should operate on original newlines
    # before hard break handling normalizes explicit breaks
    wrapper = line_wrap_to_width(width, len_fn, escape_word=markdown_escape_word)
    return _add_markdown_hard_break_handling(add_tag_newline_handling(wrapper))


def line_wrap_by_sentence(
    split_sentences: SentenceSplitter = split_sentences_no_min_length,
    width: int = DEFAULT_WRAP_WIDTH,
    min_line_len: int = DEFAULT_MIN_LINE_LEN,
    len_fn: Callable[[str], int] = DEFAULT_LEN_FUNCTION,
    escape_word: Callable[[str], str] = keep_word,
) -> LineWrapper:
    """
    Wrap lines of text to a given width but also keep sentences on their own lines.
    If the last line ends up shorter than `min_line_len`, it's combined with the
    next sentence. `escape_word` rewrites the first word of each line after the
    first; pass `markdown_escape_word` for Markdown.
    """

    def line_wrapper(text: str, initial_indent: str, subsequent_indent: str) -> str:
        # Whitespace between words is spelling, normalized as every other mode does;
        # whitespace inside a code span or math was hidden by the renderer.
        text = normalize_whitespace(text)

        sentences = split_sentences(text)

        # Handle width <= 0 as "semantic-only: split sentences, no column wrapping"
        if width <= 0:
            result = "\n".join(_escape_line_start(s.strip(), escape_word) if index else s.strip() for index, s in enumerate(s for s in sentences if s.strip()))
            if initial_indent and result:
                indented_lines = result.split("\n")
                indented_lines[0] = initial_indent + indented_lines[0]
                if subsequent_indent and len(indented_lines) > 1:
                    indented_lines[1:] = [subsequent_indent + line for line in indented_lines[1:]]
                result = "\n".join(indented_lines)
            return result

        lines: list[str] = []
        first_line = True
        length = len_fn
        initial_indent_len = len_fn(initial_indent)
        subsequent_indent_len = len_fn(subsequent_indent)

        for sentence in sentences:
            current_column = initial_indent_len if first_line else subsequent_indent_len
            if len(lines) > 0 and length(lines[-1]) < min_line_len:
                current_column += length(lines[-1])
            elif lines and sentence.strip():
                # The sentence starts a line after a line break, so its first word
                # is escaped before wrapping measures it.
                sentence = _escape_line_start(sentence.lstrip(), escape_word)

            wrapped = wrap_paragraph_lines(
                sentence,
                width=width,
                initial_column=current_column,
                subsequent_offset=subsequent_indent_len,
                escape_word=escape_word,
            )
            # If last line is shorter than min_line_len, combine with next line.
            # Also handles if the first word doesn't fit.
            if len(lines) > 0 and wrapped and length(lines[-1]) < min_line_len and length(lines[-1]) + 1 + length(wrapped[0]) <= width:
                lines[-1] += " " + wrapped[0]
                wrapped.pop(0)

            lines.extend(wrapped)

            first_line = False

        # Now insert the indents and assemble the paragraph.
        if initial_indent and len(lines) > 0:
            lines[0] = initial_indent + lines[0]
        if subsequent_indent and len(lines) > 1:
            lines[1:] = [subsequent_indent + line for line in lines[1:]]

        result = "\n".join(lines)

        # Restore original adjacency for paired tags (remove spaces added during tokenization)
        return denormalize_adjacent_tags(result)

    return line_wrapper

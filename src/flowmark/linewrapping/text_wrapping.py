from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from typing import Protocol

from flowmark.linewrapping.atomic_patterns import (
    TEMPLATE_TAG_PATTERNS,
    iter_atomic_words,
)
from flowmark.linewrapping.tag_handling import (
    denormalize_adjacent_tags,
    normalize_adjacent_tags,
)

DEFAULT_LEN_FUNCTION = len
"""
Default length function to use for wrapping.
By default this is just character length, but this can be overridden, for example
to use a smarter function that does not count ANSI escape codes.
"""


class WordSplitter(Protocol):
    def __call__(self, text: str) -> list[str]: ...


def simple_word_splitter(text: str) -> list[str]:
    """
    Split words on whitespace. This is like Python's normal `textwrap`.
    """
    return text.split()


class _HtmlMdWordSplitter:
    """
    Word splitter for Markdown/HTML that keeps certain constructs together.

    This handles LINE WRAPPING, not Markdown parsing. The distinction matters:
    - Markdown parsing (Pandoc's reader): interprets code spans, escapes, and line
      breaks
    - Line wrapping (this code): decides where to break lines in source text

    Splits on whitespace via `iter_atomic_words`, which keeps template and HTML tags
    whole. Code spans, math, raw TeX, and links reach this already free of
    whitespace: the sourced wrapper hides the whitespace inside each atom Pandoc
    locates (`flowmark.pandoc_source`).
    """

    def __call__(self, text: str) -> list[str]:
        # Normalize adjacent tags so paired tags tokenize as separate words.
        text = normalize_adjacent_tags(text)
        return [word.text for word in iter_atomic_words(text, TEMPLATE_TAG_PATTERNS)]


@cache
def get_html_md_word_splitter() -> WordSplitter:
    """
    Get cached word splitter instance. Thread-safe via @cache decorator.
    """
    return _HtmlMdWordSplitter()


# Pattern to identify words that need escaping if they start a wrapped markdown line.
# Matches list markers (*, +, -) bare or before a space (but not before a letter for
# example), blockquotes (> ), headings (#, ##, etc.).
_md_specials_pat = re.compile(r"^([-*+>]|#+)$")

# Ordered-list markers as pandoc's `markdown` reads them at the start of a line inside
# a list item (pandoc manual, "Ordered lists": `fancy_lists`, `example_lists`, and
# `startnum`): a number, a lowercase letter, or `#`, before `.` or `)`; an uppercase
# letter before `)` (before `.` it needs two spaces, which a wrap never leaves).
_md_numeral_pat = re.compile(r"^(?:[0-9]+|[a-z]|#)[.)]$|^[A-Z]\)$")
# The same markers enclosed in parentheses, and example-list labels: `(1)`, `(a)`, `(@)`.
_md_enclosed_numeral_pat = re.compile(r"^\((?:[0-9]+|[A-Za-z]|@[\w-]*)\)$")


def markdown_escape_word(word: str) -> str:
    """
    Prepends a backslash to a word if it matches markdown patterns
    that need escaping at the start of a wrapped line.
    For ordered-list markers ending in `.` or `)` ("1.", "a)"), inserts the backslash
    before that character; for enclosed ones ("(1)", "(@)"), before the `(`.
    """
    if _md_numeral_pat.match(word):
        # Insert backslash before the `.` or `)`
        return word[:-1] + "\\" + word[-1]
    elif _md_enclosed_numeral_pat.match(word) or _md_specials_pat.match(word):
        return "\\" + word
    return word


def normalize_whitespace(text: str) -> str:
    """
    Collapse each run of whitespace to one space.
    """
    return re.sub(r"\s+", " ", text)


def keep_word(word: str) -> str:
    """
    Leave a word as it is at the start of a wrapped line (plain text).
    """
    return word


def wrap_paragraph_lines(
    text: str,
    width: int,
    initial_column: int = 0,
    subsequent_offset: int = 0,
    splitter: WordSplitter | None = None,
    len_fn: Callable[[str], int] = DEFAULT_LEN_FUNCTION,
    escape_word: Callable[[str], str] = keep_word,
) -> list[str]:
    r"""
    Wrap a single paragraph of text, returning a list of wrapped lines.
    Rewritten to simplify and generalize Python's textwrap.py.

    `escape_word` rewrites the first word of each wrapped line after the first; pass
    `markdown_escape_word` for Markdown, so a wrapped word such as `-` or `1.` does not
    start a list item. Whitespace inside an atomic word (a tag or comment) is kept;
    pass `normalize_whitespace(text)` to collapse it.
    """
    # Handle width <= 0 as "no wrapping".
    if width <= 0:
        text = text.strip()
        return [text] if text else []

    # Use provided splitter or get cached one
    if splitter is None:
        splitter = get_html_md_word_splitter()

    words = splitter(text)

    lines: list[str] = []
    current_line: list[str] = []
    current_width = initial_column
    first_line = True

    # Walk through words, breaking them into lines.
    for word in words:
        word_width = len_fn(word)

        space_width = 1 if current_line else 0
        if current_width + word_width + space_width <= width:
            # Add word to current line.
            current_line.append(word)
            current_width += word_width + space_width
        else:
            # Start a new line.
            if current_line:
                lines.append(" ".join(current_line).strip())
                first_line = False

            # Check if word needs escaping at the start of this wrapped line.
            escaped_word = word if first_line else escape_word(word)

            # Start the new line with the (potentially escaped) word
            current_line = [escaped_word]
            current_width = subsequent_offset + len_fn(escaped_word)

    # Add the last line if necessary.
    if current_line:
        lines.append(" ".join(current_line).strip())

    return lines


def wrap_paragraph(
    text: str,
    width: int,
    initial_indent: str = "",
    subsequent_indent: str = "",
    initial_column: int = 0,
    word_splitter: WordSplitter | None = None,
    len_fn: Callable[[str], int] = DEFAULT_LEN_FUNCTION,
    escape_word: Callable[[str], str] = keep_word,
) -> str:
    """
    Wrap lines of a single paragraph of plain text, returning a new string.
    """
    lines = wrap_paragraph_lines(
        text=text,
        width=width,
        splitter=word_splitter,
        initial_column=initial_column + len_fn(initial_indent),
        subsequent_offset=len_fn(subsequent_indent),
        len_fn=len_fn,
        escape_word=escape_word,
    )
    # Now insert indents on first and subsequent lines, if needed.
    if initial_indent and initial_column == 0 and len(lines) > 0:
        lines[0] = initial_indent + lines[0]
    if subsequent_indent and len(lines) > 1:
        lines[1:] = [subsequent_indent + line for line in lines[1:]]
    result = "\n".join(lines)

    # Restore original adjacency for paired tags (remove spaces added during tokenization)
    return denormalize_adjacent_tags(result)

"""Source-positioned TeX-math recognition ported from Pandoc's Markdown reader.

This module is deliberately the only math-boundary recognizer used by Flowmark's
linter.  It is a literal behavioral port of Pandoc 3.10.2 commit
``f2ee5dfee866aab007a33552acc6bc01810c6918``:

* ``Text/Pandoc/Parsing/Math.hs``: ``mathInlineWith``, ``mathDisplayWith``,
  ``mathInline``, and ``mathDisplay``;
* ``Text/Pandoc/Parsing/General.hs``: ``spaceChar``, ``isSpaceChar``, and
  ``blankline``;
* ``Text/Pandoc/Shared.hs``: ``trimMath``.

Pandoc's Markdown inline dispatcher tries display math before inline math at a
``$`` and math before escape/raw-TeX handling at a backslash.  The scanner keeps
that precedence and returns source ranges so diagnostics never need to recreate
delimiter rules with regexes.

The optional ``blocked`` bitmap is for source already owned by a higher-level
literal construct (frontmatter, fenced code, or an inline code span).  Only the
candidate opener position is consulted: once Pandoc starts a math parser, math
owns its interior even when that interior contains characters which would have
started another inline construct outside math.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass


PANDOC_MATH_REFERENCE_COMMIT = "f2ee5dfee866aab007a33552acc6bc01810c6918"


@dataclass(frozen=True)
class PandocMathSpan:
    """One Math node recognized by Pandoc's Markdown math grammar."""

    start: int
    end: int
    content_start: int
    content_end: int
    display: bool
    opener: str
    closer: str
    equation: str


def _is_blocked(blocked: Sequence[int] | None, offset: int) -> bool:
    return blocked is not None and 0 <= offset < len(blocked) and bool(blocked[offset])


def _is_ascii_ws(char: str) -> bool:
    """Pandoc Shared.hs ``isWS`` / Parsing.General ``isSpaceChar``."""
    return char in " \t\r\n"


def _trim_math(text: str) -> str:
    """Port of Pandoc ``Text.Pandoc.Shared.trimMath``."""
    text = text.lstrip(" \t\r\n")
    if not text:
        return text

    end = len(text)
    while end > 0 and _is_ascii_ws(text[end - 1]):
        end -= 1
    if end == len(text):
        return text
    if end > 0 and text[end - 1] == "\\":
        # trimMath retains one trailing whitespace character after a backslash
        # so TeX's explicit control-space survives.
        return text[:end] + text[-1]
    return text[:end]


def _escaped_at(text: str, offset: int) -> bool:
    backslashes = 0
    probe = offset - 1
    while probe >= 0 and text[probe] == "\\":
        backslashes += 1
        probe -= 1
    return backslashes % 2 == 1


def _balanced_text_command_end(text: str, start: int) -> int | None:
    """End of Pandoc math's special ``\\text{...}`` branch, if complete."""
    if not text.startswith("text{", start):
        return None
    depth = 0
    escaped = False
    cursor = start + len("text")
    while cursor < len(text):
        char = text[cursor]
        if escaped:
            escaped = False
            cursor += 1
            continue
        if char == "\\":
            escaped = True
            cursor += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return None


def _blankline_end(text: str, start: int) -> int | None:
    """Port of ``blankline = skipSpaces >> newline`` (spaces/tabs only)."""
    cursor = start
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    return cursor + 1 if cursor < len(text) and text[cursor] == "\n" else None


def _inline_math_at(
    text: str, start: int, opener: str, closer: str
) -> PandocMathSpan | None:
    """Port of Pandoc ``mathInlineWith`` at one exact source position."""
    if not text.startswith(opener, start):
        return None
    cursor = start + len(opener)
    if opener == "$" and cursor < len(text) and text[cursor].isspace():
        return None

    content_start = cursor
    consumed = False
    while cursor < len(text):
        # many1Till tests the terminator before parsing the next content token.
        if text.startswith(closer, cursor):
            if not consumed:
                return None
            end = cursor + len(closer)
            if end < len(text) and text[end].isdigit():
                return None
            raw = text[content_start:cursor]
            return PandocMathSpan(
                start=start,
                end=end,
                content_start=content_start,
                content_end=cursor,
                display=False,
                opener=opener,
                closer=closer,
                equation=_trim_math(raw),
            )

        char = text[cursor]
        if char == "\\":
            if cursor + 1 >= len(text):
                return None
            # Pandoc special-cases \text{...} because dollars and backslash
            # delimiters inside text-mode content do not close math.
            text_end = _balanced_text_command_end(text, cursor + 1)
            if text_end is not None:
                cursor = text_end
            else:
                # The fallback branch is exactly '\\' >> anyChar.  If a
                # malformed \text command failed after consuming "text", try
                # backtracks and this fallback consumes only the first 't'.
                cursor += 2
            consumed = True
            continue

        if char in " \t":
            end = cursor + 1
            while end < len(text) and text[end] in " \t":
                end += 1
            # ``many1 spaceChar <* notFollowedBy (char '$')``.  Note that this
            # condition is literally dollar-specific even for \(...\) math.
            if end < len(text) and text[end] == "$":
                return None
            cursor = end
            consumed = True
            continue

        if char == "\n":
            after = cursor + 1
            # ``blankline <* notFollowedBy' blankline`` forbids an empty
            # physical line inside inline math.
            if _blankline_end(text, after) is not None:
                return None
            if after < len(text) and text[after] == "$":
                return None
            cursor = after
            consumed = True
            continue

        if _is_ascii_ws(char):
            # A bare carriage return is not accepted by any mathInlineWith
            # content branch. Pandoc's ordinary source path normalizes line
            # endings, but declining here is the faithful source-level choice.
            return None

        cursor += 1
        consumed = True
    return None


def _display_math_at(
    text: str, start: int, opener: str, closer: str
) -> PandocMathSpan | None:
    """Port of Pandoc ``mathDisplayWith`` at one exact source position."""
    if not text.startswith(opener, start):
        return None
    cursor = start + len(opener)
    content_start = cursor
    consumed = False
    while cursor < len(text):
        if text.startswith(closer, cursor):
            if not consumed:
                return None
            end = cursor + len(closer)
            return PandocMathSpan(
                start=start,
                end=end,
                content_start=content_start,
                content_end=cursor,
                display=True,
                opener=opener,
                closer=closer,
                equation=text[content_start:cursor],
            )
        if text[cursor] == "\n":
            after = cursor + 1
            if _blankline_end(text, after) is not None:
                return None
            cursor = after
            consumed = True
            continue
        cursor += 1
        consumed = True
    return None


def pandoc_math_at(text: str, start: int) -> PandocMathSpan | None:
    """Recognize the Math node Pandoc would try at ``start``, if any."""
    if start < 0 or start >= len(text) or _escaped_at(text, start):
        return None
    if text.startswith("$$", start):
        display = _display_math_at(text, start, "$$", "$$")
        if display is not None:
            return display
    if text[start] == "$":
        return _inline_math_at(text, start, "$", "$")
    if text.startswith("\\[", start):
        return _display_math_at(text, start, "\\[", "\\]")
    if text.startswith("\\(", start):
        return _inline_math_at(text, start, "\\(", "\\)")
    return None


def iter_pandoc_math_spans(
    text: str,
    *,
    blocked: Sequence[int] | None = None,
) -> Iterator[PandocMathSpan]:
    """Yield non-overlapping Pandoc math spans in source order."""
    cursor = 0
    while cursor < len(text):
        if _is_blocked(blocked, cursor):
            cursor += 1
            continue
        char = text[cursor]
        if char != "$" and char != "\\":
            cursor += 1
            continue
        span = pandoc_math_at(text, cursor)
        if span is None:
            cursor += 1
            continue
        yield span
        cursor = span.end


__all__ = (
    "PANDOC_MATH_REFERENCE_COMMIT",
    "PandocMathSpan",
    "iter_pandoc_math_spans",
    "pandoc_math_at",
)

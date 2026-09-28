"""
Auto-formatting of Markdown text.

This is similar to what is offered by
[markdownfmt](https://github.com/shurcooL/markdownfmt) but with a few adaptations,
including more aggressive normalization and support for wrapping of lines
semi-semantically (e.g. on sentence boundaries when appropriate).
(See [here](https://github.com/shurcooL/markdownfmt/issues/17) for some old
discussion on why line wrapping this way is convenient.)
"""

from __future__ import annotations

import sys

from flowmark.formats.frontmatter import split_frontmatter
from flowmark.formats.options import DEFAULT_OPTIONS, FormatOptions
from flowmark.linewrapping.protocols import LineWrapper
from flowmark.pandoc_reader import pandoc_executable
from flowmark.pandoc_source import format_sourced_markdown
from flowmark.pandoc_verify import check_meaning_preserved


def _strip_blank_edges(text: str) -> str:
    """
    Drop leading and trailing blank lines, leaving the first content line's own
    indentation intact.

    A plain `.strip()` here would take that indentation with it, and four spaces
    are the only thing marking an indented code block, so stripping silently
    demotes a leading code block to a paragraph.
    """
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def format_markdown(
    markdown_text: str,
    options: FormatOptions = DEFAULT_OPTIONS,
    line_wrapper: LineWrapper | None = None,
) -> str:
    """
    Normalize and wrap Markdown text as `options` selects, without checking that
    the result reads as the input does; `fill_markdown` checks it.

    Wraps lines and adds line breaks within paragraphs and on best-guess
    estimations of sentences, to make diffs more readable. `line_wrapper`, when
    given, wraps every paragraph in place of the wrapping `options.wrap` selects.

    Template tags (Markdoc, Jinja, HTML comments) are always treated atomically
    and never broken across lines.

    Preserves YAML frontmatter (delimited by --- lines) if present at the
    beginning of the document.
    """
    # Extract frontmatter before any processing
    frontmatter, content = split_frontmatter(markdown_text)

    # Only format the content part if there's frontmatter
    if frontmatter:
        markdown_text = content

    markdown_text = _strip_blank_edges(markdown_text) + "\n"

    result, joined = format_sourced_markdown(
        markdown_text,
        pandoc_executable(),
        options,
        line_wrapper,
    )
    if joined:
        print(
            f"Note: closed up {joined} line break{'s' if joined != 1 else ''} that fell after a hyphen",
            file=sys.stderr,
        )

    # End on exactly one newline. Some block renderers append a trailing blank
    # line as a separator from whatever follows; when the block is the document's
    # last, that separator has nothing to separate and shows up as trailing blank
    # lines in the file.
    result = result.rstrip("\n") + "\n"

    # Reattach frontmatter if it was present, with a blank line separator
    if frontmatter:
        result = frontmatter + "\n" + result

    return result


def fill_markdown(
    markdown_text: str,
    options: FormatOptions = DEFAULT_OPTIONS,
    line_wrapper: LineWrapper | None = None,
) -> str:
    """
    `format_markdown`, checked: raise `MeaningChangedError` if Pandoc reads the
    result differently than the input, beyond the normalizations flowmark makes.
    """
    result = format_markdown(markdown_text, options, line_wrapper)
    if result != markdown_text:
        check_meaning_preserved(markdown_text, result)
    return result

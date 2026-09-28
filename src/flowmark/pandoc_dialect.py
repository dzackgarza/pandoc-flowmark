"""Canonical Pandoc reader dialect for Flowmark linting and formatting."""

from __future__ import annotations


PANDOC_FORMAT = (
    "markdown"
    "+fenced_divs"
    "+raw_tex"
    "+tex_math_dollars"
    "+tex_math_single_backslash"
    "+wikilinks_title_after_pipe"
    "+autolink_bare_uris"
    "+flowmark_tags"
)
"""The exact Pandoc Markdown reader for formatting, linting, and verification.

Pandoc's ``markdown`` defaults already enable citations, pipe/grid tables,
footnotes, bracketed spans, and attributes. ``tex_math_single_backslash`` and
``wikilinks_title_after_pipe`` are not defaults and are load-bearing for this
corpus, so no caller may silently fall back to bare ``markdown``.
``autolink_bare_uris`` reads a bare URL as a link, and ``flowmark_tags`` (an
extension of the pandoc-flowmark reader) reads a template tag line as a block.
"""

PANDOC_LINT_FORMAT = PANDOC_FORMAT + "-native_divs"
"""Pandoc reader used for lint semantics.

``native_divs`` converts raw HTML ``<div>`` elements into the same AST ``Div``
node used by fenced divs and discards their source provenance. Structural lint
rules target authored fenced divs, so lint parsing disables that conversion.
Formatting/verification keep :data:`PANDOC_FORMAT` unchanged.
"""

__all__ = ("PANDOC_FORMAT", "PANDOC_LINT_FORMAT")

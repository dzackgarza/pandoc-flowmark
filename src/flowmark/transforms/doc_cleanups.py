"""Source edits for optional Markdown cleanup rules."""

from __future__ import annotations

from flowmark.formats.flowmark_markdown import SourcedDocument
from flowmark.pandoc_reader import pandoc_executable, read_source_ast
from flowmark.pandoc_source import (
    join_sourced_hyphen_breaks,
    unbold_sourced_headings,
)

_SUSPENSION_WORDS = frozenset({"and", "or", "to", "nor", "but", "through", "versus"})


def unbold_headings(document: SourcedDocument) -> None:
    """Remove strong markup that spans an entire Pandoc heading."""
    executable = pandoc_executable()
    document.source = unbold_sourced_headings(document.source, executable)
    document.ast = read_source_ast(document.source, executable)


def join_hyphen_line_breaks(document: SourcedDocument) -> int:
    """Join source soft breaks after eligible hyphens."""
    executable = pandoc_executable()
    document.source, count = join_sourced_hyphen_breaks(document.source, executable)
    document.ast = read_source_ast(document.source, executable)
    return count


def doc_cleanups(document: SourcedDocument) -> int:
    """Apply optional source cleanup rules and return the join count."""
    unbold_headings(document)
    return join_hyphen_line_breaks(document)

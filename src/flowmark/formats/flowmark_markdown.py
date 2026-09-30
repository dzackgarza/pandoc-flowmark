"""Pandoc-backed Markdown formatting for the public Python API."""

from __future__ import annotations

from dataclasses import dataclass

from flowmark.formats.options import FormatOptions, ListSpacing
from flowmark.linewrapping.line_wrappers import line_wrap_by_sentence
from flowmark.linewrapping.protocols import LineWrapper
from flowmark.linewrapping.text_filling import DEFAULT_WRAP_WIDTH
from flowmark.linewrapping.text_wrapping import markdown_escape_word
from flowmark.pandoc_reader import (
    located_source_nodes,
    PandocJson,
    pandoc_executable,
    read_source_ast,
)


@dataclass
class SourcedDocument:
    """A Pandoc parse together with its authored Markdown source."""

    source: str
    ast: PandocJson


@dataclass(frozen=True)
class FlowmarkMarkdown:
    """Parse with Pandoc and format edits against its source positions."""

    line_wrapper: LineWrapper
    list_spacing: ListSpacing

    def parse(self, source: str) -> SourcedDocument:
        return SourcedDocument(source, read_source_ast(source, pandoc_executable()))

    def render(self, document: SourcedDocument) -> str:
        from flowmark.linewrapping.markdown_filling import fill_markdown

        result = fill_markdown(
            document.source,
            FormatOptions(list_spacing=self.list_spacing),
            self.line_wrapper,
        )
        top_level = [
            node
            for node in located_source_nodes(result, pandoc_executable())
            if not node.ancestors
        ]
        if top_level and top_level[-1].node.get("t") == "Header":
            return result + "\n"
        return result

    def __call__(self, source: str) -> str:
        return self.render(self.parse(source))


def flowmark_markdown(
    line_wrapper: LineWrapper | None = None,
    list_spacing: ListSpacing = ListSpacing.loose,
) -> FlowmarkMarkdown:
    """Return the Pandoc-backed Markdown formatter."""
    wrapper = line_wrapper or line_wrap_by_sentence(
        width=DEFAULT_WRAP_WIDTH, escape_word=markdown_escape_word
    )
    return FlowmarkMarkdown(wrapper, list_spacing)

"""Markdown formatting options shared by the reader and formatter."""

from dataclasses import dataclass, field
from enum import StrEnum

from flowmark.linewrapping.text_filling import DEFAULT_WRAP_WIDTH


class ListSpacing(StrEnum):
    """How to space list items in formatted Markdown."""

    preserve = "preserve"
    loose = "loose"
    tight = "tight"


class Pass(StrEnum):
    """An optional normalization pass of the Markdown pipeline."""

    cleanups = "cleanups"
    """Unbold headings and close line breaks after a hyphen."""
    smartquotes = "smartquotes"
    """Write straight quotes and apostrophes as typographic ones."""
    ellipses = "ellipses"
    """Write three dots as an ellipsis character."""


@dataclass(frozen=True)
class Plain:
    """Treat the input as plain text, not Markdown, and wrap it to `width`."""

    width: int = DEFAULT_WRAP_WIDTH


@dataclass(frozen=True)
class Width:
    """Wrap Markdown paragraphs to `width`; a width of 0 or less leaves lines as written."""

    width: int = DEFAULT_WRAP_WIDTH


@dataclass(frozen=True)
class Semantic:
    """Start each sentence on a new line and wrap it to `width`; 0 or less means no width."""

    width: int = DEFAULT_WRAP_WIDTH


type WrapMode = Plain | Width | Semantic


@dataclass(frozen=True)
class FormatOptions:
    """How to format a document."""

    wrap: WrapMode = Width()
    passes: frozenset[Pass] = field(default_factory=frozenset[Pass])
    list_spacing: ListSpacing = ListSpacing.loose


DEFAULT_OPTIONS = FormatOptions()

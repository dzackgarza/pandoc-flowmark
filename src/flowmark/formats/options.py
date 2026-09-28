"""Markdown formatting options shared by the reader and formatter."""

from enum import StrEnum


class ListSpacing(StrEnum):
    """How to space list items in formatted Markdown."""

    preserve = "preserve"
    loose = "loose"
    tight = "tight"

"""
Test headings inside blockquotes (issue #12).

A heading inside a blockquote must be followed by a `> `-prefixed blank
line, not a bare blank line: the bare line ends the blockquote and splits
it in two, and the next formatting pass removes the line again, so the
document oscillates and never reaches a fixed point.
"""

from textwrap import dedent

from flowmark import FormatOptions, Semantic
from flowmark.linewrapping.markdown_filling import fill_markdown


def test_heading_in_blockquote_keeps_quote_intact() -> None:
    input_doc = dedent(
        """\
        > ## Heading
        > - item
        """
    )
    once = fill_markdown(input_doc, FormatOptions(Semantic()))
    # The blank line the heading inserts must stay inside the quote.
    assert "\n\n" not in once.strip(), (
        f"blockquote split by unprefixed blank line:\n{once}"
    )
    assert fill_markdown(once, FormatOptions(Semantic())) == once, (
        "formatting must be idempotent"
    )


def test_heading_in_blockquote_before_paragraph() -> None:
    input_doc = dedent(
        """\
        > ### Note
        > Some explanatory text that follows the heading.
        """
    )
    once = fill_markdown(input_doc, FormatOptions(Semantic()))
    assert "\n\n" not in once.strip(), (
        f"blockquote split by unprefixed blank line:\n{once}"
    )
    assert fill_markdown(once, FormatOptions(Semantic())) == once, (
        "formatting must be idempotent"
    )


def test_heading_in_callout_keeps_callout_intact() -> None:
    input_doc = dedent(
        """\
        > [!example] Title
        > Text.
        >
        > ## Heading
        > - item one
        """
    )
    once = fill_markdown(input_doc, FormatOptions(Semantic()))
    assert "\n\n" not in once.strip(), (
        f"callout split by unprefixed blank line:\n{once}"
    )
    assert fill_markdown(once, FormatOptions(Semantic())) == once, (
        "formatting must be idempotent"
    )


def test_heading_at_top_level_still_gets_blank_line() -> None:
    input_doc = dedent(
        """\
        ## Heading

        Text.
        """
    )
    once = fill_markdown(input_doc, FormatOptions(Semantic()))
    assert "## Heading\n\nText." in once
    assert fill_markdown(once, FormatOptions(Semantic())) == once

"""
Test list spacing behavior with different modes: preserve, loose, and tight.

This test documents the expected behavior for blank line spacing in markdown lists,
with control over tight vs loose list formatting.

- preserve: Keep lists tight or loose as authored
- loose: Convert all lists to loose format (blank lines between items) (default)
- tight: Convert all lists to tight format where possible
"""

from textwrap import dedent

from flowmark import FormatOptions, Semantic
from flowmark.formats.options import ListSpacing
from flowmark.linewrapping.markdown_filling import fill_markdown

# --- Tests for preserve mode ---


def test_tight_list_preserved() -> None:
    """Tight lists stay tight in preserve mode."""
    input_doc = "- one\n- two\n- three\n"
    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.preserve))
    assert output == "- one\n- two\n- three\n"


def test_loose_list_preserved() -> None:
    """Loose lists stay loose in preserve mode."""
    input_doc = "- one\n\n- two\n\n- three\n"
    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.preserve))
    assert output == "- one\n\n- two\n\n- three\n"


def test_loose_is_default() -> None:
    """Loose is the default: list spacing is normalized, not left to the document."""
    input_tight = "- one\n- two\n- three\n"
    input_loose = "- one\n\n- two\n\n- three\n"

    # Without explicit list_spacing, tight is normalized to loose
    assert fill_markdown(input_tight) == "- one\n\n- two\n\n- three\n"
    # Without explicit list_spacing, loose stays loose
    assert fill_markdown(input_loose) == "- one\n\n- two\n\n- three\n"


def test_numbered_list_preserve() -> None:
    """Numbered lists preserve their tightness."""
    input_tight = "1. one\n2. two\n3. three\n"
    input_loose = "1. one\n\n2. two\n\n3. three\n"

    assert (
        fill_markdown(input_tight, FormatOptions(list_spacing=ListSpacing.preserve))
        == "1. one\n2. two\n3. three\n"
    )
    assert (
        fill_markdown(input_loose, FormatOptions(list_spacing=ListSpacing.preserve))
        == "1. one\n\n2. two\n\n3. three\n"
    )


# --- Tests for loose mode ---


def test_tight_list_to_loose() -> None:
    """Tight lists become loose in loose mode."""
    input_doc = "- one\n- two\n- three\n"
    output = fill_markdown(input_doc)
    assert output == "- one\n\n- two\n\n- three\n"


def test_loose_list_stays_loose() -> None:
    """Loose lists stay loose in loose mode."""
    input_doc = "- one\n\n- two\n\n- three\n"
    output = fill_markdown(input_doc)
    assert output == "- one\n\n- two\n\n- three\n"


def test_numbered_list_to_loose() -> None:
    """Numbered lists become loose in loose mode."""
    input_doc = "1. one\n2. two\n3. three\n"
    output = fill_markdown(input_doc)
    assert output == "1. one\n\n2. two\n\n3. three\n"


# --- Tests for tight mode ---


def test_loose_list_to_tight() -> None:
    """Loose lists become tight in tight mode."""
    input_doc = "- one\n\n- two\n\n- three\n"
    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.tight))
    assert output == "- one\n- two\n- three\n"


def test_tight_list_stays_tight() -> None:
    """Tight lists stay tight in tight mode."""
    input_doc = "- one\n- two\n- three\n"
    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.tight))
    assert output == "- one\n- two\n- three\n"


def test_multi_para_stays_loose_in_tight_mode() -> None:
    """Multi-paragraph items stay loose even in tight mode (CommonMark requirement)."""
    input_doc = (
        dedent(
            """
        - para1

          para2
        - item2
        """
        ).strip()
        + "\n"
    )

    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.tight))
    # Item with multiple paragraphs forces loose
    assert "\n\n" in output


# --- Tests for nested lists ---


def test_nested_lists_independent_preserve() -> None:
    """Each nested list independently preserves its tightness."""
    input_doc = (
        dedent(
            """
        - outer tight
          - inner tight
          - inner tight
        - outer tight
        """
        ).strip()
        + "\n"
    )

    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.preserve))
    # Both outer and inner should remain tight
    expected = (
        dedent(
            """
        - outer tight
          - inner tight
          - inner tight
        - outer tight
        """
        ).strip()
        + "\n"
    )
    assert output == expected


def test_nested_lists_loose_outer_tight_inner() -> None:
    """Loose outer list with tight inner list."""
    input_doc = (
        dedent(
            """
        - outer loose

          - inner tight
          - inner tight

        - outer loose
        """
        ).strip()
        + "\n"
    )

    output = fill_markdown(input_doc, FormatOptions(list_spacing=ListSpacing.preserve))
    # Outer should be loose, inner should be tight
    expected = (
        dedent(
            """
        - outer loose

          - inner tight
          - inner tight

        - outer loose
        """
        ).strip()
        + "\n"
    )
    assert output == expected


# --- Tests for complex content (code blocks, quotes) ---


def test_list_items_with_code_blocks_preserve() -> None:
    """List items with code blocks preserve tightness in preserve mode."""
    input_doc = (
        dedent(
            """
        - Use `z` (zoxide) instead of `cd`.

          ```shell
          z ~/some/long/path/to/foo
          ```

        - Use `eza` instead of `ls`.
        """
        ).strip()
        + "\n"
    )

    expected_doc = (
        dedent(
            """
        - Use `z` (zoxide) instead of `cd`.

          ```shell
          z ~/some/long/path/to/foo
          ```

        - Use `eza` instead of `ls`.
        """
        ).strip()
        + "\n"
    )

    # This is loose in the input, should stay loose
    normalized_doc = fill_markdown(
        input_doc, FormatOptions(Semantic(), list_spacing=ListSpacing.preserve)
    )
    assert normalized_doc == expected_doc


def test_list_items_with_code_blocks_loose() -> None:
    """List items with code blocks get proper spacing in loose mode."""
    input_doc = (
        dedent(
            """
        - Use `z` (zoxide) instead of `cd`.

          ```shell
          z ~/some/long/path/to/foo
          ```

        - Use `eza` instead of `ls`. It has color support.
        """
        ).strip()
        + "\n"
    )

    expected_doc = (
        dedent(
            """
        - Use `z` (zoxide) instead of `cd`.

          ```shell
          z ~/some/long/path/to/foo
          ```

        - Use `eza` instead of `ls`. It has color support.
        """
        ).strip()
        + "\n"
    )

    normalized_doc = fill_markdown(input_doc, FormatOptions(Semantic()))
    assert normalized_doc == expected_doc


def test_list_items_with_quote_blocks() -> None:
    """Test that list items with quote blocks get proper spacing."""
    input_doc = (
        dedent(
            """
        - First item with a quote.

          > This is a quote block.
          > With multiple lines.

        - Second item without quotes.
        """
        ).strip()
        + "\n"
    )

    expected_doc = (
        dedent(
            """
        - First item with a quote.

          > This is a quote block.
          > With multiple lines.

        - Second item without quotes.
        """
        ).strip()
        + "\n"
    )

    # This is loose in the input (has multi-block items)
    normalized_doc = fill_markdown(
        input_doc, FormatOptions(Semantic(), list_spacing=ListSpacing.preserve)
    )
    assert normalized_doc == expected_doc


# --- Tests for spacing normalization with loose mode ---


def test_input_spacing_normalization_loose() -> None:
    """Test that various input spacings normalize to loose output in loose mode."""
    # One newline between items (tight markdown)
    input_tight = "- First item\n- Second item\n- Third item\n"

    # Two newlines between items (loose)
    input_loose = "- First item\n\n- Second item\n\n- Third item\n"

    # Three newlines between items (extra spacing)
    input_extra = "- First item\n\n\n- Second item\n\n\n- Third item\n"

    # All should normalize to loose output
    expected_output = "- First item\n\n- Second item\n\n- Third item\n"

    assert fill_markdown(input_tight) == expected_output
    assert fill_markdown(input_loose) == expected_output
    assert fill_markdown(input_extra) == expected_output


def test_input_spacing_normalization_tight() -> None:
    """Test that various input spacings normalize to tight output in tight mode."""
    # One newline between items (tight markdown)
    input_tight = "- First item\n- Second item\n- Third item\n"

    # Two newlines between items (loose)
    input_loose = "- First item\n\n- Second item\n\n- Third item\n"

    # Three newlines between items (extra spacing)
    input_extra = "- First item\n\n\n- Second item\n\n\n- Third item\n"

    # All should normalize to tight output
    expected_output = "- First item\n- Second item\n- Third item\n"

    assert (
        fill_markdown(input_tight, FormatOptions(list_spacing=ListSpacing.tight))
        == expected_output
    )
    assert (
        fill_markdown(input_loose, FormatOptions(list_spacing=ListSpacing.tight))
        == expected_output
    )
    assert (
        fill_markdown(input_extra, FormatOptions(list_spacing=ListSpacing.tight))
        == expected_output
    )


def test_complex_content_with_loose_mode() -> None:
    """Test that complex content gets proper spacing in loose mode."""
    input_doc = (
        dedent(
            """
        - Item before code
        - Item with code

          ```shell
          echo "test"
          ```
        - Item after code
        """
        ).strip()
        + "\n"
    )

    expected_output = (
        dedent(
            """
        - Item before code

        - Item with code

          ```shell
          echo "test"
          ```

        - Item after code
        """
        ).strip()
        + "\n"
    )

    assert fill_markdown(input_doc, FormatOptions(Semantic())) == expected_output


def test_multi_paragraph_spacing_loose_mode() -> None:
    """Test that multi-paragraph items get consistent spacing in loose mode."""
    input_doc = (
        dedent(
            """
        - Simple item
        - Multi-paragraph item

          Second paragraph
        - Another simple item
        """
        ).strip()
        + "\n"
    )

    expected_output = (
        dedent(
            """
        - Simple item

        - Multi-paragraph item

          Second paragraph

        - Another simple item
        """
        ).strip()
        + "\n"
    )

    assert fill_markdown(input_doc, FormatOptions(Semantic())) == expected_output

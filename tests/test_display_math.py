"""
Test display math block handling (\\\\[...\\\\] and $$...$$).

These constructs are Pandoc/LaTeX extensions beyond CommonMark/GFM.
Flowmark's CustomDisplayMath block element must recognize them as
block-level constructs so they never pass through paragraph wrapping
or hard-break normalization.
"""

from textwrap import dedent

from flowmark import FormatOptions, Semantic
from flowmark.linewrapping.markdown_filling import fill_markdown


def test_display_math_no_blank_line_before() -> None:
    """
    \\[...\\] without a blank line before it must still be recognized as
    a display math block.  The \\[ line must NOT get a hard-break backslash
    appended (the ``\\[  `` with two trailing spaces bug).
    """
    input_doc = dedent(
        """\
        Some text:
        \\[  
        f(x) = 2
        \\]
        More text.
        """
    ).strip()

    result = fill_markdown(input_doc, FormatOptions(Semantic()))

    # The critical assertion: \\[ must NOT have a trailing backslash.
    assert "\\[\\\n" not in result, (
        f"\\[ must not have trailing hard-break backslash, got:\n{repr(result)}"
    )

    # \\[ should start a block on its own line.
    assert "\n\\[\n" in result, (
        f"\\[ should appear on its own line, got:\n{repr(result)}"
    )


def test_display_math_preserves_content_verbatim() -> None:
    """Content within \\[...\\] must be preserved as-is, not line-wrapped."""
    input_doc = dedent(
        """\
        Text.

        \\[
        a & b \\\\
        c & d
        \\]

        More text.
        """
    ).strip()

    result = fill_markdown(input_doc, FormatOptions(Semantic()))

    # The ampersands and double-backslash must survive intact
    assert "a & b \\\\" in result, (
        f"display math content with & must be preserved, got:\n{repr(result)}"
    )
    assert "c & d" in result, (
        f"display math content with & must be preserved, got:\n{repr(result)}"
    )


def test_display_math_with_blank_line_before() -> None:
    """\\[...\\] with a blank line before it: standard block display math."""
    input_doc = dedent(
        """\
        Text.

        \\[
        f(x) = 2
        \\]

        More text.
        """
    ).strip()

    result = fill_markdown(input_doc, FormatOptions(Semantic()))

    # Should be a clean three-line block
    assert "\n\\[\n" in result
    assert "\n\\]\n" in result


def test_dollar_display_math() -> None:
    """$$...$$ must also be recognized as display math block."""
    input_doc = dedent(
        """\
        Text.

        $$
        f(x) = 2
        $$

        More text.
        """
    ).strip()

    result = fill_markdown(input_doc, FormatOptions(Semantic()))

    assert "\n$$\n" in result
    assert "\n$$\n" in result  # opener and closer both present
    assert "f(x) = 2" in result

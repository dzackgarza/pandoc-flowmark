from dataclasses import replace
from textwrap import dedent

import pytest

from flowmark import FormatOptions, Semantic, Width, markdown_line_wrap_to_width
from flowmark.linewrapping.markdown_filling import fill_markdown
from flowmark.linewrapping.tag_handling import add_tag_newline_handling
from flowmark.linewrapping.text_wrapping import (
    get_html_md_word_splitter,
    markdown_escape_word,
    simple_word_splitter,
    wrap_paragraph,
    wrap_paragraph_lines,
)
from flowmark.reformat_api import REFORMAT_DEFAULTS, reformat_text


def test_markdown_escape_word_function() -> None:
    # Cases that should be escaped
    assert markdown_escape_word("-") == "\\-"
    assert markdown_escape_word("+") == "\\+"
    assert markdown_escape_word("*") == "\\*"
    assert markdown_escape_word(">") == "\\>"
    assert markdown_escape_word("#") == "\\#"
    assert markdown_escape_word("##") == "\\##"
    assert markdown_escape_word("1.") == "1\\."
    assert markdown_escape_word("10.") == "10\\."
    assert markdown_escape_word("1)") == "1\\)"
    assert markdown_escape_word("99)") == "99\\)"

    # Cases that should NOT be escaped
    assert markdown_escape_word("word") == "word"
    assert markdown_escape_word("-word") == "-word"  # Starts with char, but not just char
    assert markdown_escape_word("word-") == "word-"  # Ends with char
    assert markdown_escape_word("#word") == "#word"
    assert markdown_escape_word("word#") == "word#"
    assert markdown_escape_word("1.word") == "1.word"
    assert markdown_escape_word("word1.") == "word1."
    assert markdown_escape_word("1)word") == "1)word"
    assert markdown_escape_word("word1)") == "word1)"
    assert markdown_escape_word("<tag>") == "<tag>"  # Other symbols
    assert markdown_escape_word("[link]") == "[link]"
    assert markdown_escape_word("1") == "1"  # Just number
    assert markdown_escape_word(".") == "."  # Just dot


def test_wrap_paragraph_lines_markdown_escaping() -> None:
    assert wrap_paragraph_lines(text="- word", width=10, escape_word=markdown_escape_word) == ["- word"]

    text = "word - word * word + word > word # word ## word 1. word 2) word"

    assert wrap_paragraph_lines(text=text, width=5, escape_word=markdown_escape_word) == [
        "word",
        "\\-",
        "word",
        "\\*",
        "word",
        "\\+",
        "word",
        "\\>",
        "word",
        "\\#",
        "word",
        "\\##",
        "word",
        "1\\.",
        "word",
        "2\\)",
        "word",
    ]
    assert wrap_paragraph_lines(text=text, width=10, escape_word=markdown_escape_word) == [
        "word -",
        "word *",
        "word +",
        "word >",
        "word #",
        "word ##",
        "word 1.",
        "word 2)",
        "word",
    ]
    assert wrap_paragraph_lines(text=text, width=15, escape_word=markdown_escape_word) == [
        "word - word *",
        "word + word >",
        "word # word ##",
        "word 1. word 2)",
        "word",
    ]
    assert wrap_paragraph_lines(text=text, width=20, escape_word=markdown_escape_word) == [
        "word - word * word +",
        "word > word # word",
        "\\## word 1. word 2)",
        "word",
    ]
    assert wrap_paragraph_lines(text=text, width=20) == [
        "word - word * word +",
        "word > word # word",
        "## word 1. word 2)",
        "word",
    ]

    test2 = """Testing - : Is Ketamine Contraindicated in Patients with Psychiatric Disorders? - REBEL EM - more words - accessed April 24, 2025, <https://rebelem.com/is-ketamine-contraindicated-in-patients-with-psychiatric-disorders/>"""
    assert wrap_paragraph_lines(text=test2, width=80, escape_word=markdown_escape_word) == [
        "Testing - : Is Ketamine Contraindicated in Patients with Psychiatric Disorders?",
        "\\- REBEL EM - more words - accessed April 24, 2025,",
        "<https://rebelem.com/is-ketamine-contraindicated-in-patients-with-psychiatric-disorders/>",
    ]


def test_smart_splitter() -> None:
    splitter = get_html_md_word_splitter()

    html_text = "This is <span class='test'>some text</span> and <a href='#'>this is a link</a>."
    assert splitter(html_text) == [
        "This",
        "is",
        "<span class='test'>some",
        "text</span>",
        "and",
        "<a href='#'>this",
        "is",
        "a",
        "link</a>.",
    ]


@pytest.mark.parametrize(
    "construct",
    [
        "`code with spaces`",
        "`<!-- not a real comment -->`",
        "(`<!--% ... -->`).",
        "[Markdown link](https://example.com)",
        "![an image alt](https://example.com/a.png)",
        "[@coble1919, p. 33]",
        "[see @dolgachev2016; @cossec1989, ch. 2]",
        "[[Moduli of Enriques Surfaces]]",
        "[[Enriques surfaces#Coble surfaces|the Coble case]]",
    ],
)
def test_wrapping_never_breaks_inside_a_parsed_inline_construct(construct: str) -> None:
    """
    A code span, link, image, pandoc citation or wikilink is one element to the
    parser, so at a width far narrower than it the wrapper moves it whole rather
    than break at its spaces (#28: what is unbreakable comes from the parse).
    """
    result = fill_markdown(
        f"Some words before {construct} and some words after.\n",
        FormatOptions(Width(12)),
    )

    assert any(construct in line for line in result.splitlines()), result


def test_wrap_text() -> None:
    sample_text = (
        "This is a sample text with a [Markdown-link](https://example.com)"
        " and an <a href='#'>tag</a>. It should demonstrate the functionality of "
        "our enhanced text wrapping implementation."
    )

    print("\nFilled text with default splitter:")
    filled = wrap_paragraph(
        sample_text,
        word_splitter=simple_word_splitter,
        width=40,
        initial_indent=">",
        subsequent_indent=">>",
    )
    print(filled)
    filled_expected = dedent(
        """
        >This is a sample text with a
        >>[Markdown-link](https://example.com)
        >>and an <a href='#'>tag</a>. It should
        >>demonstrate the functionality of our
        >>enhanced text wrapping implementation.
        """
    ).strip()

    print("\nFilled text with get_html_md_word_splitter():")
    filled_smart = wrap_paragraph(
        sample_text,
        word_splitter=get_html_md_word_splitter(),
        width=40,
        initial_indent=">",
        subsequent_indent=">>",
    )
    print(filled_smart)
    filled_smart_expected = dedent(
        """
        >This is a sample text with a
        >>[Markdown-link](https://example.com)
        >>and an <a href='#'>tag</a>. It should
        >>demonstrate the functionality of our
        >>enhanced text wrapping implementation.
        """
    ).strip()

    print("\nFilled text with get_html_md_word_splitter() and initial_offset:")
    filled_smart_offset = wrap_paragraph(
        sample_text,
        word_splitter=get_html_md_word_splitter(),
        width=40,
        initial_indent=">",
        subsequent_indent=">>",
        initial_column=35,
    )
    print(filled_smart_offset)
    filled_smart_offset_expected = dedent(
        """
        This
        >>is a sample text with a
        >>[Markdown-link](https://example.com)
        >>and an <a href='#'>tag</a>. It should
        >>demonstrate the functionality of our
        >>enhanced text wrapping implementation.
        """
    ).strip()

    assert filled == filled_expected
    assert filled_smart == filled_smart_expected
    assert filled_smart_offset == filled_smart_offset_expected


def test_wrap_width() -> None:
    text = dedent(
        """
        You may also simply ask a question and the kmd assistant will help you. Press
        `?` or just press space twice, then write your question or request. Press `?` and
        tab to get suggested questions.
        """
    ).strip()
    width = 80
    wrapped = wrap_paragraph_lines(text, width=width)
    print(wrapped)
    print([len(line) for line in wrapped])
    assert all(len(line) <= width for line in wrapped)


def test_line_wrap_to_width_with_markdown_breaks() -> None:
    # Get a markdown-aware line wrapper
    wrapper = markdown_line_wrap_to_width(width=80)

    # Test trailing space line breaks
    text_with_spaces = "This line ends with spaces  \nThis is a new line"
    wrapped_spaces = wrapper(text_with_spaces, initial_indent="", subsequent_indent="")
    assert wrapped_spaces == "This line ends with spaces\\\nThis is a new line"

    # Test backslash line breaks
    text_with_backslash = "This line ends with backslash\\\nThis is a new line"
    wrapped_backslash = wrapper(text_with_backslash, initial_indent="", subsequent_indent="")
    assert wrapped_backslash == "This line ends with backslash\\\nThis is a new line"

    # Test wrapping with indentation
    indented_wrapper = markdown_line_wrap_to_width(width=40)
    long_text = "This is a very long line that will be wrapped and it ends with a line break  \nNext line with content that continues"
    wrapped_long = indented_wrapper(long_text, initial_indent="  ", subsequent_indent="    ")
    assert wrapped_long == ("  This is a very long line that will be\n    wrapped and it ends with a line\n    break\\\n    Next line with content that\n    continues")

    # Test different indentation for segments
    mixed_indent_wrapper = markdown_line_wrap_to_width(width=30)
    mixed_indent_text = "First segment  \nSecond segment\\\nThird segment"
    wrapped_mixed_indent = mixed_indent_wrapper(mixed_indent_text, initial_indent="* ", subsequent_indent="  ")
    assert wrapped_mixed_indent == ("* First segment\\\n  Second segment\\\n  Third segment")

    # Test empty segments
    empty_segment_text = "Before  \n\\\nAfter"
    wrapped_empty = wrapper(empty_segment_text, initial_indent="", subsequent_indent="")
    assert wrapped_empty == "Before\\\n\\\nAfter"

    # Test single segment (no line breaks)
    single_segment = "Text with no breaks"
    wrapped_single = wrapper(single_segment, initial_indent="> ", subsequent_indent="  ")
    assert wrapped_single == "> Text with no breaks"


def test_template_tag_splitter() -> None:
    """Test that template tags (Markdoc/Jinja/Nunjucks) are kept as atomic tokens."""
    splitter = get_html_md_word_splitter()

    # Markdoc-style tags: {% tag %}
    markdoc_text = "Text with {% if $condition %} template tags {% endif %} here."
    result = splitter(markdoc_text)
    assert "{% if $condition %}" in result
    assert "{% endif %}" in result

    # Self-closing Markdoc tags: {% tag /%}
    self_closing = "Include {% partial file='header.md' /%} here."
    result = splitter(self_closing)
    assert "{% partial file='header.md' /%}" in result

    # Jinja/Nunjucks comments: {# comment #}
    comment_text = "Text with {# this is a comment #} inline."
    result = splitter(comment_text)
    assert "{# this is a comment #}" in result

    # Jinja/Nunjucks variables: {{ variable }}
    variable_text = "Hello {{ user.name }} welcome."
    result = splitter(variable_text)
    assert "{{ user.name }}" in result

    # Complex Markdoc tag with attributes
    complex_tag = "Use {% callout type='warning' title='Note' %} for emphasis."
    result = splitter(complex_tag)
    assert "{% callout type='warning' title='Note' %}" in result

    # Multiple template tags in sequence (with spaces between)
    multi_tag = "{% if $a %} {% if $b %}nested{% /if %} {% /if %}"
    result = splitter(multi_tag)
    # Tags should be kept together
    assert "{% if $a %}" in result
    assert "{% /if %}" in result


def test_template_tag_wrapping() -> None:
    """Test that template tags don't break across lines during wrapping."""

    # Template tag should stay together even if it's long
    text_with_tag = "Some text {% callout type='warning' %} more text after the tag."
    result = wrap_paragraph_lines(text=text_with_tag, width=30, escape_word=markdown_escape_word)

    # The tag should not be split across lines
    full_result = " ".join(result)
    assert "{% callout type='warning' %}" in full_result

    # Jinja variable should stay together
    text_with_var = "Hello {{ user.first_name }} and welcome to the site."
    result = wrap_paragraph_lines(text=text_with_var, width=25, escape_word=markdown_escape_word)
    full_result = " ".join(result)
    assert "{{ user.first_name }}" in full_result

    # Comment should stay together
    text_with_comment = "Text {# TODO: fix this later #} and more text here."
    result = wrap_paragraph_lines(text=text_with_comment, width=20, escape_word=markdown_escape_word)
    full_result = " ".join(result)
    assert "{# TODO: fix this later #}" in full_result


def test_mixed_html_and_template_tags() -> None:
    """Test that HTML tags and template tags work together."""
    splitter = get_html_md_word_splitter()

    mixed = "Text <span class='x'>html</span> and {% if $y %} template {% endif %} here."
    result = splitter(mixed)

    # HTML should be coalesced
    assert "<span class='x'>html</span>" in result
    # Template tags should be kept together
    assert "{% if $y %}" in result
    assert "{% endif %}" in result


def test_long_template_tags() -> None:
    """Test that tags with many attributes (10+ words) are kept together."""
    splitter = get_html_md_word_splitter()

    # 10-word template tag
    long_tag = "{% component name='widget' type='button' size='large' color='blue' disabled=true %}"
    text = f"Before {long_tag} after."
    result = splitter(text)
    assert long_tag in result

    # 12-word template tag (at the limit)
    very_long_tag = "{% table columns=[a, b, c] rows=[1, 2, 3] border=true striped=true hover=true %}"
    text = f"Before {very_long_tag} after."
    result = splitter(text)
    assert very_long_tag in result


def test_long_html_tags() -> None:
    """Test that HTML tags with many attributes are kept together."""
    splitter = get_html_md_word_splitter()

    # Long HTML tag with many attributes
    long_html = "<div class='container' id='main' data-value='test' style='color: red'>content</div>"
    text = f"Before {long_html} after."
    result = splitter(text)
    assert long_html in result


def test_long_jinja_comments() -> None:
    """Test that long Jinja comments are kept together."""
    splitter = get_html_md_word_splitter()

    # Long comment with many words (12 words = MAX_TAG_WORDS)
    long_comment = "{# This is a long comment that spans many words here #}"
    text = f"Before {long_comment} after."
    result = splitter(text)
    assert long_comment in result


def test_html_comments_kept_together() -> None:
    """Test that HTML comments are kept as atomic units."""
    splitter = get_html_md_word_splitter()

    # Simple HTML comment
    comment = "<!-- a comment -->"
    text = f"Text with {comment} inline."
    result = splitter(text)
    assert comment in result

    # Longer HTML comment
    long_comment = "<!-- this is a longer comment with more words -->"
    text2 = f"Before {long_comment} after."
    result2 = splitter(text2)
    assert long_comment in result2


def test_single_word_inline_code_not_coalesced() -> None:
    """
    Test that single-word inline code spans do NOT incorrectly coalesce with following text.

    Regression test for bug where `getRequiredEnv()` would be coalesced with words
    following it, causing incorrect line breaks before the inline code.
    """
    splitter = get_html_md_word_splitter()

    # Single-word inline code should stay as one word, not coalesce with following text
    text = "access env vars via `getRequiredEnv()` and must live in files"
    result = splitter(text)

    # The backticked code should be its own separate token
    assert "`getRequiredEnv()`" in result

    # Find the token containing the inline code
    code_token = next(r for r in result if "`getRequiredEnv()`" in r)
    # It should be EXACTLY the inline code, not merged with other words
    assert code_token == "`getRequiredEnv()`", f"Expected exact match, got {code_token!r}"

    # "and" should be a separate word
    assert "and" in result


def test_multiple_single_word_inline_codes() -> None:
    """
    Test text with multiple single-word inline code spans.
    """
    splitter = get_html_md_word_splitter()

    text = 'via `getRequiredEnv()` and must live in files with `"use node"`.'
    result = splitter(text)

    # Each inline code should be separate
    assert "`getRequiredEnv()`" in result
    # The second code span with quotes should be handled correctly too
    # Note: this has internal spaces so may be multiple words, but should not
    # incorrectly coalesce with "via" or "and"
    assert "via" in result
    assert "and" in result


def test_newline_after_opening_tag() -> None:
    """
    Test that newlines after opening Jinja/Markdoc tags are preserved.

    When a tag is followed by a newline, the content should start on a new line.
    """
    from flowmark.linewrapping.line_wrappers import (
        line_wrap_by_sentence,
    )

    # Test with line_wrap_to_width
    wrapper = markdown_line_wrap_to_width(width=80)

    # Opening tag followed by newline and content
    text = "{% description ref='example' %}\nThis is content after the tag."
    result = wrapper(text, "", "")
    # The newline after the tag should be preserved
    assert "{% description ref='example' %}\n" in result or result.startswith("{% description ref='example' %}\n")

    # HTML comment tag followed by newline
    text2 = "<!-- f:description ref='example' -->\nContent after HTML comment tag."
    result2 = wrapper(text2, "", "")
    assert "<!-- f:description ref='example' -->\n" in result2

    # Test with line_wrap_by_sentence
    wrapper2 = add_tag_newline_handling(line_wrap_by_sentence(width=80, escape_word=markdown_escape_word))
    result3 = wrapper2(text, "", "")
    assert "{% description ref='example' %}\n" in result3


def test_newline_before_closing_tag() -> None:
    """
    Test that newlines before closing Jinja/Markdoc tags are preserved.

    When a closing tag is preceded by a newline, it should stay on its own line.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Content followed by newline and closing tag
    text = "Some content here.\n{% /description %}"
    result = wrapper(text, "", "")
    # The closing tag should be on its own line
    assert "\n{% /description %}" in result

    # HTML comment closing tag
    text2 = "Some content.\n<!-- /f:description -->"
    result2 = wrapper(text2, "", "")
    assert "\n<!-- /f:description -->" in result2


def test_paired_tags_not_broken() -> None:
    """
    Test that paired tags on the same line stay together during wrapping.

    Common pattern: {% field %}{% /field %} for empty fields.
    """
    splitter = get_html_md_word_splitter()

    # Paired Jinja tags - the opening+closing pair is kept as a single token
    paired = "{% field kind='string' id='email' %}{% /field %}"
    text = f"Some text before {paired} and after."
    result = splitter(text)
    # The pair is kept together as a single token (with normalized space between)
    full_result = " ".join(result)
    assert "{% field kind='string' id='email' %}" in full_result
    assert "{% /field %}" in full_result

    # HTML comment paired tags - kept together as a single token
    paired_html = "<!-- f:field kind='string' --><!-- /f:field -->"
    text2 = f"Before {paired_html} after."
    result2 = splitter(text2)
    full_result2 = " ".join(result2)
    assert "<!-- f:field kind='string' -->" in full_result2
    assert "<!-- /f:field -->" in full_result2

    # Wrapping should not break either tag in a pair
    long_text = f"This is a longer piece of text with {paired} embedded in the middle."
    wrapped = wrap_paragraph_lines(text=long_text, width=40, escape_word=markdown_escape_word)
    full_result = " ".join(wrapped)
    # Both tags should be intact (not broken across lines)
    assert "{% field kind='string' id='email' %}" in full_result
    assert "{% /field %}" in full_result


def test_nested_tags_newlines_preserved() -> None:
    """
    Test that newlines between nested tags are preserved.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Nested structure with newlines
    text = "{% form id='test' %}\n{% group id='section' %}\n{% field id='name' %}{% /field %}\n{% /group %}\n{% /form %}"
    result = wrapper(text, "", "")

    # Each tag should be on its own line
    assert "{% form id='test' %}\n" in result
    assert "\n{% group id='section' %}\n" in result
    assert "\n{% /group %}\n" in result
    assert "\n{% /form %}" in result


def test_backslash_in_tag_attributes() -> None:
    r"""
    Test that backslashes in tag attribute values are preserved.

    Common case: regex patterns like pattern="^[^@]+\.[^@]+$"
    """
    splitter = get_html_md_word_splitter()

    # Tag with regex pattern containing backslash
    tag_with_backslash = r"{% field pattern='^[^@]+\.[^@]+$' %}"
    text = f"Use {tag_with_backslash} for email."
    result = splitter(text)

    # The backslash should be preserved
    assert tag_with_backslash in result

    # In wrapped output
    wrapped = wrap_paragraph_lines(text=text, width=80, escape_word=markdown_escape_word)
    full_result = " ".join(wrapped)
    assert r"\." in full_result


def test_tag_with_list_items() -> None:
    """
    Test that tags containing lists don't merge with list items.

    The closing tag should stay on its own line, not merge with last list item.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Simulating what happens when a paragraph contains tag + list + closing tag
    # Note: In real Markdown, lists are separate blocks, but we test the wrapping behavior
    text = "- Option A {% #option_a %}\n{% /field %}"
    result = wrapper(text, "", "")

    # The closing tag should NOT be merged onto the list item line
    assert "\n{% /field %}" in result


def test_table_inside_tags_is_a_table() -> None:
    """
    A table between tag-only lines is set off from them by blank lines, so it is
    read as a table and rendered as one, delimiter row normalized.
    """
    from flowmark import fill_markdown

    text = "{% field %}\n| A | B |\n|---|---|\n| 1 | 2 |\n{% /field %}\n"

    assert fill_markdown(text) == ("{% field %}\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n{% /field %}\n")


# pandoc reads table rows written straight after paragraph text as more of the
# paragraph, so they are kept as written: never wrapped (#36), and never rewritten,
# the delimiter row included.
WIDE_TABLE = dedent("""
    | Quarter | Revenue ($M) | YoY % | QoQ % | Segment A % | Segment B % | Geo: US % | Geo: Intl % |
    |---------|-------------|-------|-------|-------------|-------------|-----------|-------------|
    | Q1 2025 | 125.3 | +12% | +3% | 45% | 55% | 60% | 40% |
    """).strip()


def test_table_rows_after_paragraph_text_kept_as_written() -> None:
    from flowmark import fill_markdown

    text = "Some text\n| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n"

    assert fill_markdown(text) == text


def test_wide_table_rows_after_paragraph_text_not_wrapped() -> None:
    """The reproduction case from #36: the header row is wider than the wrap width."""
    from flowmark import fill_markdown

    text = f"Paragraph text here.\n{WIDE_TABLE}\n"

    assert fill_markdown(text, FormatOptions(Width(88))) == text


def test_table_rows_after_paragraph_text_semantic_wrapping() -> None:
    from flowmark import fill_markdown

    text = "The first sentence of this paragraph. The second sentence, before the rows."

    assert fill_markdown(f"{text}\n{WIDE_TABLE}\n", FormatOptions(Semantic(88))) == (
        f"The first sentence of this paragraph.\nThe second sentence, before the rows.\n{WIDE_TABLE}\n"
    )


def test_table_not_wrapped_at_narrow_width() -> None:
    from flowmark import fill_markdown

    text = "| A long header | Another long header |\n|---|---|\n| Cell data | More cell data |\n"

    assert fill_markdown(text, FormatOptions(Width(40))) == ("| A long header | Another long header |\n| --- | --- |\n| Cell data | More cell data |\n")


def test_tag_wrapper_list_items() -> None:
    """
    Test that list items inside tags have their newlines preserved.

    Newlines around list items are preserved when tags are present.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # List inside tags WITHOUT blank lines
    text = "{% field %}\n- Item 1\n- Item 2\n- Item 3\n{% /field %}"
    result = wrapper(text, "", "")

    # Each list item should be on its own line
    assert "{% field %}\n" in result
    assert "\n- Item 1\n" in result
    assert "\n- Item 2\n" in result
    assert "\n- Item 3\n" in result
    assert "\n{% /field %}" in result


def test_tag_wrapper_splits_list_items_only_with_tags() -> None:
    """
    Normal markdown text with lists should NOT have list items treated as
    segment boundaries: only when tags are present.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # List WITHOUT tags - list items should NOT be treated as segment boundaries
    text = "Some text\n- list item\nMore text"
    result = wrapper(text, "", "")

    # Without tags, list items are merged with surrounding text
    assert "- list item" in result
    # The list item might be merged with surrounding text (no segment break)
    assert "\n- list item\n" not in result


def test_lone_table_row_in_paragraph_text_wraps() -> None:
    """
    A pipe row with no delimiter row under it is no table, to pandoc or to
    flowmark's parser, so it wraps with the paragraph it is part of.
    """
    from flowmark import fill_markdown

    assert fill_markdown("Some text\n| A | B |\nMore text\n") == ("Some text | A | B | More text\n")


def test_table_rows_between_paragraph_lines_inside_tags() -> None:
    """
    Paragraph text, table rows, and more paragraph text between two tags is one
    paragraph to pandoc: the rows keep their lines and are not rewritten.
    """
    from flowmark import fill_markdown

    text = "{% field %}\nIntro text here.\n| Col1 | Col2 |\n|------|------|\nOutro text.\n{% /field %}\n"

    assert fill_markdown(text) == text


def test_tag_wrapper_blank_line_normalization() -> None:
    """
    Test that block content between tags gets exactly one blank line at boundaries.

    This ensures:
    - One blank line after opening tag before list/table
    - One blank line after list/table before closing tag

    This prevents CommonMark lazy continuation from merging tags into blocks.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # List between tags - should get blank lines around it
    text = "{% field %}\n- Item 1\n- Item 2\n{% /field %}"
    result = wrapper(text, "", "")

    # Verify blank line after opening tag (two newlines = blank line)
    assert "{% field %}\n\n" in result, f"Expected blank line after opening tag, got: {result}"

    # Verify blank line before closing tag
    assert "\n\n{% /field %}" in result, f"Expected blank line before closing tag, got: {result}"


def test_tag_wrapper_table_blank_lines() -> None:
    """
    Test blank line normalization specifically for tables.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Table between tags
    text = "{% field %}\n| A | B |\n|---|---|\n{% /field %}"
    result = wrapper(text, "", "")

    # Should have blank lines around table
    assert "{% field %}\n\n" in result
    assert "\n\n{% /field %}" in result


def test_tag_wrapper_preserves_existing_blank_lines() -> None:
    """
    Test that if there are already blank lines, we don't add extras.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Already has blank lines
    text = "{% field %}\n\n- Item 1\n\n{% /field %}"
    result = wrapper(text, "", "")

    # Should still have exactly one blank line (not doubled)
    # Note: the wrapper may normalize, so we just check it's not more than 2 newlines
    assert "{% field %}\n\n" in result
    lines = result.split("\n")
    # Count consecutive empty lines - should not exceed 1
    max_consecutive_empty = 0
    current_consecutive = 0
    for line in lines:
        if line.strip() == "":
            current_consecutive += 1
            max_consecutive_empty = max(max_consecutive_empty, current_consecutive)
        else:
            current_consecutive = 0
    assert max_consecutive_empty <= 1, f"Too many consecutive blank lines: {result}"


def test_self_closing_jinja_tags() -> None:
    """
    Test self-closing Jinja tags (tags without a separate closing tag).

    Examples: {% break %}, {% continue %}, {% include "file" %}, {% set x = 1 %}
    These should be kept atomic and preserve newlines around them.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Self-closing tag on its own line
    text = "Some content.\n{% break %}\nMore content."
    result = wrapper(text, "", "")
    assert "\n{% break %}\n" in result

    # Self-closing tag with attributes
    text2 = "Before.\n{% include 'header.html' %}\nAfter."
    result2 = wrapper(text2, "", "")
    assert "\n{% include 'header.html' %}\n" in result2

    # Multiple self-closing tags
    text3 = "{% set x = 1 %}\n{% set y = 2 %}\n{% set z = 3 %}"
    result3 = wrapper(text3, "", "")
    assert "{% set x = 1 %}\n" in result3
    assert "\n{% set y = 2 %}\n" in result3
    assert "\n{% set z = 3 %}" in result3

    # Self-closing tag inline with text (should stay together)
    splitter = get_html_md_word_splitter()
    inline = "Use {% include 'partial.html' %} to include."
    tokens = splitter(inline)
    assert "{% include 'partial.html' %}" in tokens


def test_self_closing_html_comment_tags() -> None:
    """
    Test self-closing HTML comment tags (comments without a closing counterpart).

    Examples: <!-- note -->, <!-- TODO: fix this -->, <!-- @annotation -->
    These should be kept atomic and preserve newlines around them.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Self-closing comment on its own line
    text = "Some content.\n<!-- note: important -->\nMore content."
    result = wrapper(text, "", "")
    assert "\n<!-- note: important -->\n" in result

    # Comment with longer content
    text2 = "Before.\n<!-- TODO: refactor this section later -->\nAfter."
    result2 = wrapper(text2, "", "")
    assert "\n<!-- TODO: refactor this section later -->\n" in result2

    # Multiple self-closing comments
    text3 = "<!-- start -->\nContent here.\n<!-- end -->"
    result3 = wrapper(text3, "", "")
    assert "<!-- start -->\n" in result3
    assert "\n<!-- end -->" in result3

    # Self-closing comment inline (should stay together)
    splitter = get_html_md_word_splitter()
    inline = "See <!-- ref: section 3 --> for details."
    tokens = splitter(inline)
    assert "<!-- ref: section 3 -->" in tokens


def test_self_closing_jinja_variable_tags() -> None:
    """
    Test Jinja variable tags {{ ... }} which are always self-closing.

    Examples: {{ name }}, {{ user.email }}, {{ items | length }}
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Variable tag on its own line
    text = "Name:\n{{ user.name }}\nEmail:"
    result = wrapper(text, "", "")
    assert "\n{{ user.name }}\n" in result

    # Variable tag with filter
    text2 = "Count:\n{{ items | length }}\nDone."
    result2 = wrapper(text2, "", "")
    assert "\n{{ items | length }}\n" in result2

    # Variable inline (should stay together)
    splitter = get_html_md_word_splitter()
    inline = "Hello {{ name }}, welcome!"
    tokens = splitter(inline)
    assert "{{ name }}," in tokens or "{{ name }}" in tokens


def test_self_closing_jinja_comment_tags() -> None:
    """
    Test Jinja comment tags {# ... #} which are always self-closing.

    Examples: {# TODO #}, {# This is a comment #}
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Comment tag on its own line
    text = "Code here.\n{# TODO: optimize this #}\nMore code."
    result = wrapper(text, "", "")
    assert "\n{# TODO: optimize this #}\n" in result

    # Comment inline (should stay together)
    splitter = get_html_md_word_splitter()
    inline = "Value {# in bytes #} is 1024."
    tokens = splitter(inline)
    assert "{# in bytes #}" in tokens


def test_adjacent_jinja_tags_no_space() -> None:
    """
    Test that adjacent Jinja tags stay adjacent (no space inserted).

    When tags like %}{% are adjacent, normalization adds a space for tokenization,
    but denormalization must remove it in the final output.
    """
    from flowmark.linewrapping.line_wrappers import (
        line_wrap_by_sentence,
    )
    from flowmark.linewrapping.tag_handling import (
        denormalize_adjacent_tags,
        normalize_adjacent_tags,
    )

    # Test normalize/denormalize directly
    original = "{% field kind='string' %}{% /field %}"
    normalized = normalize_adjacent_tags(original)
    assert normalized == "{% field kind='string' %} {% /field %}", f"Expected space, got: {normalized}"
    denormalized = denormalize_adjacent_tags(normalized)
    assert denormalized == original, f"Expected {original}, got: {denormalized}"

    # Test with line_wrap_to_width (uses wrap_paragraph)
    wrapper1 = markdown_line_wrap_to_width(width=80)
    result1 = wrapper1(original, "", "")
    assert result1 == original, f"line_wrap_to_width: Expected {original}, got: {result1}"

    # Test with line_wrap_by_sentence (uses wrap_paragraph_lines)
    wrapper2 = add_tag_newline_handling(line_wrap_by_sentence(width=80, escape_word=markdown_escape_word))
    result2 = wrapper2(original, "", "")
    assert result2 == original, f"line_wrap_by_sentence: Expected {original}, got: {result2}"


def test_adjacent_html_comment_tags_no_space() -> None:
    """
    Test that adjacent HTML comment tags stay adjacent (no space inserted).

    This is critical for Markform-style HTML comment syntax.
    """
    from flowmark.linewrapping.line_wrappers import (
        line_wrap_by_sentence,
    )
    from flowmark.linewrapping.tag_handling import (
        denormalize_adjacent_tags,
        normalize_adjacent_tags,
    )

    # Test normalize/denormalize directly
    original = '<!-- f:field kind="string" id="name" --><!-- /f:field -->'
    normalized = normalize_adjacent_tags(original)
    assert " <!-- /f:field -->" in normalized, f"Expected space after normalization, got: {normalized}"
    denormalized = denormalize_adjacent_tags(normalized)
    assert denormalized == original, f"Expected {original}, got: {denormalized}"

    # Test with line_wrap_to_width
    wrapper1 = markdown_line_wrap_to_width(width=80)
    result1 = wrapper1(original, "", "")
    assert result1 == original, f"line_wrap_to_width: Expected {original}, got: {result1}"

    # Test with line_wrap_by_sentence
    wrapper2 = add_tag_newline_handling(line_wrap_by_sentence(width=80, escape_word=markdown_escape_word))
    result2 = wrapper2(original, "", "")
    assert result2 == original, f"line_wrap_by_sentence: Expected {original}, got: {result2}"


def test_adjacent_jinja_variable_tags_no_space() -> None:
    """
    Test that adjacent Jinja variable tags stay adjacent.
    """
    from flowmark.linewrapping.line_wrappers import line_wrap_by_sentence
    from flowmark.linewrapping.tag_handling import (
        denormalize_adjacent_tags,
        normalize_adjacent_tags,
    )

    original = "{{ a }}{{ b }}"
    normalized = normalize_adjacent_tags(original)
    assert normalized == "{{ a }} {{ b }}", f"Expected space, got: {normalized}"
    denormalized = denormalize_adjacent_tags(normalized)
    assert denormalized == original, f"Expected {original}, got: {denormalized}"

    wrapper = add_tag_newline_handling(line_wrap_by_sentence(width=80, escape_word=markdown_escape_word))
    result = wrapper(original, "", "")
    assert result == original, f"Expected {original}, got: {result}"


def test_adjacent_jinja_comment_tags_no_space() -> None:
    """
    Test that adjacent Jinja comment tags stay adjacent.
    """
    from flowmark.linewrapping.line_wrappers import line_wrap_by_sentence
    from flowmark.linewrapping.tag_handling import (
        denormalize_adjacent_tags,
        normalize_adjacent_tags,
    )

    original = "{# first #}{# second #}"
    normalized = normalize_adjacent_tags(original)
    assert normalized == "{# first #} {# second #}", f"Expected space, got: {normalized}"
    denormalized = denormalize_adjacent_tags(normalized)
    assert denormalized == original, f"Expected {original}, got: {denormalized}"

    wrapper = add_tag_newline_handling(line_wrap_by_sentence(width=80, escape_word=markdown_escape_word))
    result = wrapper(original, "", "")
    assert result == original, f"Expected {original}, got: {result}"


def test_adjacent_tags_full_pipeline() -> None:
    """
    Test adjacent tags through the full Markdown processing pipeline.

    This catches bugs where normalization happens but denormalization doesn't.
    """
    from flowmark import fill_markdown

    # Jinja tags
    jinja_input = "{% field kind='string' %}{% /field %}"
    jinja_result = fill_markdown(jinja_input, FormatOptions(Semantic()))
    assert jinja_result.strip() == jinja_input, f"Jinja: Expected {jinja_input}, got: {jinja_result.strip()}"

    # HTML comment tags
    html_input = '<!-- f:field kind="string" id="name" --><!-- /f:field -->'
    html_result = fill_markdown(html_input, FormatOptions(Semantic()))
    assert html_result.strip() == html_input, f"HTML: Expected {html_input}, got: {html_result.strip()}"

    # With surrounding text
    mixed_input = "Before {% field %}{% /field %} after."
    mixed_result = fill_markdown(mixed_input, FormatOptions(Semantic()))
    assert "{% field %}{% /field %}" in mixed_result, f"Mixed: Space inserted in: {mixed_result}"


def test_paragraph_text_no_extra_blank_lines() -> None:
    """
    Test that paragraph text between tags does NOT get extra blank lines.

    Regular paragraph text should NOT trigger blank line insertion before
    closing tags. Only block content (lists/tables) should get blank lines.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Simple paragraph text between tags - NO blank lines added
    text = "{% description %}\nThis is a simple note.\nJust paragraph text.\n{% /description %}"
    result = wrapper(text, "", "")

    # Should NOT have double newlines before the closing tag
    assert "\n\n{% /description %}" not in result, f"Unexpected blank line before closing tag: {result}"
    # The closing tag should still be on its own line
    assert "\n{% /description %}" in result

    # HTML comment version
    text2 = "<!-- f:note -->\nThis is text content.\n<!-- /f:note -->"
    result2 = wrapper(text2, "", "")
    assert "\n\n<!-- /f:note -->" not in result2, f"Unexpected blank line before closing tag: {result2}"
    assert "\n<!-- /f:note -->" in result2


def test_list_content_gets_blank_lines() -> None:
    """
    Test that list content between tags DOES get blank lines.

    List items are block content that requires blank lines to prevent
    CommonMark lazy continuation from merging tags into the list.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # List between tags - SHOULD get blank lines
    text = "{% field %}\n- Item 1\n- Item 2\n{% /field %}"
    result = wrapper(text, "", "")

    # Should have blank line after opening tag (before list)
    assert "{% field %}\n\n" in result, f"Expected blank line after opening tag: {result}"

    # Should have blank line before closing tag (after list)
    assert "\n\n{% /field %}" in result, f"Expected blank line before closing tag: {result}"


def test_table_content_gets_blank_lines() -> None:
    """
    Test that table content between tags DOES get blank lines.

    Table rows are block content that requires blank lines.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Table between tags - SHOULD get blank lines
    text = "{% field %}\n| A | B |\n|---|---|\n| 1 | 2 |\n{% /field %}"
    result = wrapper(text, "", "")

    # Should have blank line before table
    assert "{% field %}\n\n" in result, f"Expected blank line after opening tag: {result}"

    # Should have blank line before closing tag
    assert "\n\n{% /field %}" in result, f"Expected blank line before closing tag: {result}"


def test_mixed_content_blank_lines_correct() -> None:
    """
    Test that mixed content (text followed by list) gets correct blank lines.

    Only the transition between tag and block content needs blank lines.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Text then list between tags
    text = "{% field %}\nSome intro text.\n- Item 1\n- Item 2\n{% /field %}"
    result = wrapper(text, "", "")

    # Should have blank line before list (list is block content)
    # and blank line before closing tag (after list)
    assert "\n\n{% /field %}" in result, f"Expected blank line before closing tag: {result}"


def test_various_tag_types_with_tables() -> None:
    """
    Test tables with various tag types (Jinja, HTML comments, variables).

    Tables should always get blank lines regardless of tag type.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Jinja tags with table
    jinja = "{% table %}\n| A | B |\n|---|---|\n{% /table %}"
    jinja_result = wrapper(jinja, "", "")
    assert "{% table %}\n\n" in jinja_result
    assert "\n\n{% /table %}" in jinja_result

    # HTML comment tags with table
    html = "<!-- f:table -->\n| A | B |\n|---|---|\n<!-- /f:table -->"
    html_result = wrapper(html, "", "")
    assert "<!-- f:table -->\n\n" in html_result
    assert "\n\n<!-- /f:table -->" in html_result

    # Jinja variable tags (edge case - less common with tables)
    var = "{{ header }}\n| A | B |\n|---|---|\n{{ footer }}"
    var_result = wrapper(var, "", "")
    # Variable tags are tags too
    assert "{{ header }}\n\n" in var_result


def test_paragraph_only_content_various_tags() -> None:
    """
    Test paragraph-only content with various tag types.

    None of these should get extra blank lines.
    """

    wrapper = markdown_line_wrap_to_width(width=80)

    # Jinja tags
    jinja = "{% note %}\nSimple paragraph.\n{% /note %}"
    jinja_result = wrapper(jinja, "", "")
    assert "\n\n{% /note %}" not in jinja_result

    # HTML comment tags
    html = "<!-- f:warning -->\nWarning text here.\n<!-- /f:warning -->"
    html_result = wrapper(html, "", "")
    assert "\n\n<!-- /f:warning -->" not in html_result

    # Longer paragraph
    long = "{% tip %}\nThis is a longer paragraph with more text that spans across multiple sentences. It should all be wrapped normally.\n{% /tip %}"
    long_result = wrapper(long, "", "")
    assert "\n\n{% /tip %}" not in long_result


def test_a_citation_at_a_wrap_boundary_formats_with_verify() -> None:
    """
    Pandoc reads `[@coble1919, p. 33]` as one `Cite`; at width 40 it straddles the
    limit, and breaking inside it must not be how the line is filled.
    """
    source = "See the argument in [[Moduli of Enriques Surfaces]] and the bound in [@coble1919, p. 33] for details.\n"

    result = reformat_text(source, replace(REFORMAT_DEFAULTS, wrap=Width(40)))

    assert "[@coble1919, p. 33]" in result, result

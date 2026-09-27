from __future__ import annotations

import os

from flowmark.pandoc_source import (
    located_nodes,
    read_source_ast,
    wrap_plain_paragraphs,
)
from flowmark.pandoc_verify import pandoc_ast
from flowmark.reformat_api import reformat_text


def test_formatting_preserves_pandoc_div_attributes_and_raw_tex() -> None:
    source = (
        '::: {.theorem\n'
        '    title="A source-position theorem"\n'
        '    #thm:source-position\n'
        '}\n'
        'A long sentence explains the result and should wrap inside this theorem.\n'
        '\n'
        '\\begin{align*}\n'
        'a &= b\n'
        '\\end{align*}\n'
        ':::\n'
    )

    formatted = reformat_text(source, width=48, semantic=False)

    assert 'title="A source-position theorem"' in formatted
    assert '#thm:source-position' in formatted
    assert '\\begin{align*}\na &= b\n\\end{align*}' in formatted
    assert 'A long sentence explains the result and should\n' in formatted
    div = pandoc_ast(formatted)[0]
    assert isinstance(div, dict) and div.get("t") == "Div"
    contents = div.get("c")
    assert isinstance(contents, list)
    assert contents[0] == [
        "thm:source-position",
        ["theorem"],
        [["title", "A source-position theorem"]],
    ]
    assert reformat_text(formatted, width=48, semantic=False) == formatted


def test_pandoc_positions_locate_div_paragraph_and_inline_math() -> None:
    source = (
        '::: {.theorem\n'
        '    title="Range example"\n'
        '}\n'
        'A sentence with \\(x+y\\) and enough words to wrap.\n'
        ':::\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    nodes = located_nodes(read_source_ast(source, pandoc))

    paragraph = next(node for node in nodes if node.node["t"] == "Para")
    math = next(node for node in nodes if node.node["t"] == "Math")
    assert paragraph.source_range.start.line == 4
    assert paragraph.source_range.start.column == 1
    assert math.source_range.start.line == 4
    assert math.source_range.start.column == source.splitlines()[3].index(r"\(x+y\)") + 1


def test_pandoc_edit_wraps_inside_multiline_div_without_rewriting_raw_source() -> None:
    source = (
        '::: {.theorem\n'
        '    title="A source-position theorem"\n'
        '    #thm:source-position\n'
        '}\n'
        'A long sentence explains the result and should wrap inside this theorem.\n'
        '\n'
        '\\begin{align*}\n'
        'a &= b\n'
        '\\end{align*}\n'
        ':::\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 48, pandoc)

    assert 'A long sentence explains the result and should\n' in formatted
    assert 'title="A source-position theorem"\n' in formatted
    assert '#thm:source-position\n' in formatted
    assert '\\begin{align*}\na &= b\n\\end{align*}' in formatted
    assert wrap_plain_paragraphs(formatted, 48, pandoc) == formatted


def test_pandoc_edit_keeps_inline_constructs_atomic() -> None:
    source = (
        'Several words introduce [a linked phrase](https://example.org) and '
        'the value \\(x+y\\) beside `code with spaces` in this sentence.\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 48, pandoc)

    assert formatted != source
    assert '[a linked phrase](https://example.org)' in formatted
    assert '\\(x+y\\)' in formatted
    assert '`code with spaces`' in formatted
    assert wrap_plain_paragraphs(formatted, 48, pandoc) == formatted


def test_pandoc_edit_keeps_multiline_code_source() -> None:
    source = (
        'Words before a code span `first line\n'
        'second line` and enough words after it to wrap the paragraph.\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 48, pandoc)

    assert '`first line\nsecond line`' in formatted
    assert wrap_plain_paragraphs(formatted, 48, pandoc) == formatted


def test_pandoc_edit_wraps_list_item_under_its_marker() -> None:
    source = (
        '- A long first item has enough words to need a line wrap inside the list.\n'
        '\n'
        '- A second item stays separate.\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 40, pandoc)

    assert formatted.startswith('- A long first item has enough words to\n  ')
    assert '\n- A second item stays separate.\n' in formatted
    assert wrap_plain_paragraphs(formatted, 40, pandoc) == formatted


def test_pandoc_edit_wraps_quote_under_its_marker() -> None:
    source = (
        '> A quoted sentence has enough words to need a line wrap within the '
        'same block quote.\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 40, pandoc)

    assert formatted.startswith('> A quoted sentence has enough words to\n> ')
    assert wrap_plain_paragraphs(formatted, 40, pandoc) == formatted


def test_pandoc_edit_wraps_list_item_with_inline_syntax() -> None:
    source = (
        '- A long item has [a linked phrase](https://example.org) and enough '
        'words to need a line wrap inside this list.\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 50, pandoc)

    assert formatted != source
    assert '[a linked phrase](https://example.org)' in formatted
    assert formatted.splitlines()[1].startswith('  ')
    assert wrap_plain_paragraphs(formatted, 50, pandoc) == formatted


def test_pandoc_edit_preserves_hard_break_source() -> None:
    source = 'This line has a two-space line break.  \nAnd this is a regular line.\n'
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 40, pandoc)

    assert formatted != source
    assert 'break.  \nAnd' in formatted
    assert wrap_plain_paragraphs(formatted, 40, pandoc) == formatted


def test_pandoc_edit_wraps_at_sentence_boundaries() -> None:
    source = (
        'A first sentence has enough words to occupy a line. '
        'A second sentence has enough words to occupy the next line.\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")
    formatted = wrap_plain_paragraphs(source, 60, pandoc, semantic=True)

    assert 'line.\nA second sentence' in formatted
    assert wrap_plain_paragraphs(formatted, 60, pandoc, semantic=True) == formatted


def test_pandoc_edit_preserves_template_owned_lines() -> None:
    source = (
        '{% field kind="select" %}\n'
        '- [ ] First option {% first %}\n'
        '- [ ] Second option {% second %}\n'
        '{% /field %}\n'
    )
    pandoc = os.environ.get("PANDOC_SOURCEPOS_EXE", "pandoc")

    assert wrap_plain_paragraphs(source, 40, pandoc, semantic=True) == source

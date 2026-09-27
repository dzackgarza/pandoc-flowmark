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

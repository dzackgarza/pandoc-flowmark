from __future__ import annotations

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
    assert div["t"] == "Div"
    assert div["c"][0] == [
        "thm:source-position",
        ["theorem"],
        [["title", "A source-position theorem"]],
    ]
    assert reformat_text(formatted, width=48, semantic=False) == formatted

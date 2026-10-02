"""
Tests for the ambiguous-input preflight.

When verification fails, the question "did flowmark break this?" and the question
"was this already broken?" have different answers and different owners. The gate
could only ever ask the first one, so it blamed flowmark for the second -- which in
#17 cost the reporter a bisection to attribute a defect that was in their input.

The Pandoc reader is a required runtime dependency.
"""

import pytest

from flowmark.pandoc_lint import walk_pandoc
from flowmark.pandoc_verify import pandoc_ast
from flowmark.preflight import MalformedInputError, preflight
from flowmark.reformat_api import reformat_text

# Pandoc reads an unclosed fence as paragraph text.
AMBIGUOUS_FENCE = "Intro.\n\n```python\nx = 1\n\nmore   text   here\n"


def test_preflight_finds_unterminated_math() -> None:
    assert [f.line for f in preflight("A paragraph with $x + y and no closer.\n")] == [
        1
    ]


def test_preflight_finds_an_unbalanced_fence() -> None:
    assert [f.line for f in preflight("Intro.\n\n```python\nx = 1\n")] == [3]


def test_preflight_is_quiet_on_clean_input() -> None:
    """
    High precision is the whole point. A check that fires on ordinary documents
    would relabel every real flowmark bug as "your input is ambiguous", which is
    worse than the message it replaces.
    """
    clean = (
        "# Title\n\nA paragraph with $x + y$ inline math and `code | with a bar`.\n\n"
        "| a | b |\n|---|---|\n| one | two |\n\n```python\nx = 1\n```\n\n"
        "A price of $5 and another of $10.\n"
    )

    assert preflight(clean) == []


def test_unclosed_fence_is_refused() -> None:
    assert [finding.line for finding in preflight(AMBIGUOUS_FENCE)] == [3]
    with pytest.raises(MalformedInputError, match="never closed"):
        reformat_text(AMBIGUOUS_FENCE)


def test_preflight_accepts_inline_math_that_continues_on_the_next_line() -> None:
    """Pandoc reads `$a +\\nb$` as one span; neither line is an unterminated one."""
    assert preflight("A span $a +\nb$ across two lines.\n") == []


@pytest.mark.parametrize(
    "middle",
    [
        "$$\nx = y\n.$$",
        "$$\nx = y,$$",
        "$$\nx = y$$",
        "$$\nx = y\n$$.",
        "$$x = y\n$$",
        "$$x = y\n.$$",
        "$$\nx = y\n.$$\n\nNext $a$ and $$\nz\n$$ done.",
        "$$\nx = y\n\nz\n$$",
    ],
)
def test_preflight_reads_display_math_as_pandoc_does(middle: str) -> None:
    """
    Pandoc closes `$$` at the next `$$` wherever it is on the line, and a blank
    line ends the paragraph. A math finding is due exactly when pandoc keeps a
    `$` as text.
    """
    text = f"Text.\n{middle}\nMore text.\n"
    strs = [node["c"] for node in walk_pandoc(pandoc_ast(text)) if node["t"] == "Str"]
    literal_dollar = any(isinstance(s, str) and "$" in s for s in strs)

    assert bool(preflight(text)) == literal_dollar

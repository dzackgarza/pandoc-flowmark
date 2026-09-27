"""
Tests for the ambiguous-input preflight.

When verification fails, the question "did flowmark break this?" and the question
"was this already broken?" have different answers and different owners. The gate
could only ever ask the first one, so it blamed flowmark for the second -- which in
#17 cost the reporter a bisection to attribute a defect that was in their input.

The Pandoc reader is a required runtime dependency.
"""

from flowmark.preflight import preflight
from flowmark.reformat_api import reformat_text


# Pandoc reads an unclosed fence as paragraph text.
AMBIGUOUS_FENCE = "Intro.\n\n```python\nx = 1\n\nmore   text   here\n"


def test_preflight_reads_a_bar_inside_a_span_as_cell_content() -> None:
    """
    Pandoc's `markdown` reader does not split a pipe-table cell at a `|` inside a
    code span or `$...$` math, so these rows are well-formed, two cells each.
    Reporting them sent the #17 reporter and a later user to "fix" input that was
    correct -- and escaping the bar inside a code span changes the code's text.
    """
    rows = (
        "| construct | status |\n|---|---|\n"
        "| explicit `|X(F_{q^r})|` for `A^n` | proposed |\n"
        "| $|-2K_{\\widetilde V}|=\\{C\\}$ generically | established |\n"
    )

    assert preflight(rows) == []


def test_preflight_finds_a_row_whose_cell_count_disagrees() -> None:
    """Pandoc drops the third cell, so its text never reaches the output."""
    findings = preflight("| a | `b|c` |\n|---|---|\n| one | two | three |\n")

    assert [f.line for f in findings] == [3]


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


def test_unclosed_fence_follows_pandoc_paragraph_meaning() -> None:
    assert [finding.line for finding in preflight(AMBIGUOUS_FENCE)] == [3]
    assert reformat_text(AMBIGUOUS_FENCE, semantic=True, verify=True) == (
        "Intro.\n\n```python x = 1\n\nmore text here\n"
    )


def test_preflight_accepts_inline_math_that_continues_on_the_next_line() -> None:
    """Pandoc reads `$a +\\nb$` as one span; neither line is an unterminated one."""
    assert preflight("A span $a +\nb$ across two lines.\n") == []

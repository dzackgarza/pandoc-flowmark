"""
Round-trip tests for Pandoc constructs that flowmark must not alter.

Every source below is well-formed: `pandoc -f markdown` parses it with zero
warnings. These go through `reformat_text`, the entry point the CLI uses, since
some of these defects live in that pipeline rather than in the parser itself.

Flowmark is free to change a construct's spelling, so where it deliberately
normalizes (indented code to fenced, a blank line between footnote definitions)
these assert the normalized form; the rest must survive byte-identically.

Covers #5 (footnote definitions), #6 (nested divs), #7 (indented code blocks),
and #8 (raw inline TeX). Fenced div attribute specs are in test_fenced_div.py
(#3).
"""

import pytest

from flowmark.reformat_api import reformat_text

# --- #5: footnote definitions ---------------------------------------------


def test_consecutive_footnote_definitions_both_survive() -> None:
    """
    Pandoc needs no blank line between footnote definitions. Indenting the
    second made it a continuation of the first's body, deleting a footnote.

    The blank line between definitions is added -- a spelling change that keeps
    both definitions, unlike the merge it replaces.
    """
    source = "Text.[^1] More text.[^2]\n\n[^1]: First note.\n[^2]: Second note.\n"
    expected = "Text.[^1] More text.[^2]\n\n[^1]: First note.\n\n[^2]: Second note.\n"

    assert reformat_text(source) == expected


def test_footnote_definitions_separated_by_blank_line_round_trip() -> None:
    source = "Text.[^1] More text.[^2]\n\n[^1]: First note.\n\n[^2]: Second note.\n"

    assert reformat_text(source) == source


def test_multi_paragraph_footnote_definition_keeps_its_continuation() -> None:
    """
    With the label on its own line and a two-paragraph body, the second
    paragraph was de-indented out of the footnote and fenced as a code block,
    so its markup stopped parsing.
    """
    source = "T.[^1]\n\n[^1]:\n    First para.\n\n    Second para.\n"

    assert reformat_text(source) == source


def test_multi_paragraph_footnote_definition_inline_start_round_trips() -> None:
    source = "T.[^1]\n\n[^1]: First para.\n\n    Second para.\n"

    assert reformat_text(source) == source


# --- #6: nested divs -------------------------------------------------------


def test_nested_divs_do_not_gain_a_closing_fence() -> None:
    """
    The non-nesting parse closed the outer div at the inner closer, so trailing
    content escaped the parent and an extra fence was emitted.
    """
    source = "::: {.theorem}\nOuter before.\n\n::: {.proof}\nInner.\n:::\n\nOuter after.\n:::\n"

    assert reformat_text(source) == source


def test_deeply_nested_divs_round_trip() -> None:
    source = "::: {.a}\n::: {.b}\n::: {.c}\nDeep.\n:::\n:::\n:::\n"

    assert reformat_text(source) == source


def test_colon_run_inside_code_block_does_not_close_the_div() -> None:
    """
    A colon run inside a fenced code block is literal text, not a fence. Counting
    it closed the div early, leaving the code block's own closing fence to escape
    to top level: pandoc read ``["Div"]`` before and ``["Div", "Para"]`` after.
    """
    source = "::: {.foo}\n```\n:::\n```\nAfter.\n:::\n"

    assert reformat_text(source) == source


def test_div_opener_inside_code_block_does_not_inflate_depth() -> None:
    """The same defect in the other direction: the div's real closer got eaten."""
    source = "::: {.foo}\n```\n::: {.bar}\n```\nAfter.\n:::\n"

    assert reformat_text(source) == source


def test_colon_run_inside_tilde_code_block_does_not_close_the_div() -> None:
    source = "::: {.foo}\n~~~\n:::\n~~~\nAfter.\n:::\n"

    assert reformat_text(source) == source


def test_code_block_inside_nested_div_does_not_disturb_depth() -> None:
    source = "::: {.a}\n::: {.b}\n```\n:::\n```\n:::\nAfter.\n:::\n"

    assert reformat_text(source) == source


# --- #7: indented code blocks ---------------------------------------------
#
# Flowmark deliberately rewrites indented code blocks to fenced ones, which is a
# spelling change that keeps the block a code block. These assert that the block
# survives *as code* -- what regressed was a leading one silently becoming prose.


def test_indented_code_block_as_first_block_stays_code() -> None:
    """
    The four-space indent is the only thing marking the block as code, and it
    sits where the document-edge strip could reach it.
    """
    assert (
        reformat_text("    literal code\n\nAfter.\n")
        == "```\nliteral code\n```\n\nAfter.\n"
    )


def test_indented_code_block_alone_stays_code() -> None:
    """A document that is nothing but an indented code block."""
    assert reformat_text("    literal code\n") == "```\nliteral code\n```\n"


def test_indented_code_block_after_paragraph_stays_code() -> None:
    assert (
        reformat_text("Intro.\n\n    literal code\n\nAfter.\n")
        == "Intro.\n\n```\nliteral code\n```\n\nAfter.\n"
    )


def test_leading_blank_lines_are_still_stripped() -> None:
    """Blank lines at the document edges must still go."""
    assert reformat_text("\n\nIntro.\n\n\n") == "Intro.\n"


# --- #8: raw inline TeX ----------------------------------------------------


def test_raw_inline_tex_underscores_are_not_emphasis() -> None:
    """
    Two underscores spanning raw TeX were read as an emphasis pair and
    re-rendered with asterisks, producing invalid LaTeX.
    """
    source = "A \\overline{ \\mathcal{M}_{1} } b y_{2} c.\n"

    assert reformat_text(source) == source


def test_raw_inline_tex_flat_command_round_trips() -> None:
    source = "A \\overline{ M_{1} } b y_{2} c.\n"

    assert reformat_text(source) == source


def test_inline_math_with_underscores_round_trips() -> None:
    source = "A $\\overline{ \\mathcal{M}_{1} }$ b $y_{2}$ c.\n"

    assert reformat_text(source) == source


# --- #11: subscript --------------------------------------------------------


def test_subscript_survives_and_is_not_strikeout() -> None:
    """
    Pandoc's `subscript` extension (on by default for `-f markdown`) reads
    `H~2~O` as H, subscript 2, O. Parsing single tildes as GFM strikethrough
    and re-emitting them doubled turns the subscript into a strikeout -- a
    different construct entirely.
    """
    source = "H~2~O and x^2^.\n"

    assert reformat_text(source) == source


# --- #10: definition lists --------------------------------------------------


def test_definition_list_compact_round_trips() -> None:
    """
    Pandoc's `definition_lists` extension (on by default for `-f markdown`):
    the marker only means anything at line start, so rewrapping must not join
    it into the term's paragraph.
    """
    source = "Term\n:   Definition here.\n"

    assert reformat_text(source) == source


def test_definition_list_loose_round_trips() -> None:
    source = "Term 1\n\n:   Definition 1\n\nTerm 2\n\n:   Definition 2\n"

    assert reformat_text(source) == source


def test_definition_list_multiple_definitions_round_trip() -> None:
    source = "Term\n:   Def one\n:   Def two\n"

    assert reformat_text(source) == source


def test_definition_list_tilde_marker_round_trips() -> None:
    source = "Term\n~   Definition here.\n"

    assert reformat_text(source) == source


def test_definition_list_between_paragraphs_round_trips() -> None:
    source = "Before.\n\nTerm\n:   Definition.\n\nAfter paragraph.\n"

    assert reformat_text(source) == source


def test_definition_list_continuation_paragraph_round_trips() -> None:
    source = "Term\n:   First para.\n\n    Second para of same def.\n\nAfter.\n"

    assert reformat_text(source) == source


def test_definition_marker_mid_paragraph_stays_prose() -> None:
    """
    Pandoc's definition lists do NOT interrupt a paragraph: with text above
    the term, the whole thing is one Para. Flowmark must keep treating it as
    prose -- the default verify gate asserts the meaning is unchanged.
    """
    result = reformat_text("Some text\nTerm\n:   Def\n")

    assert ":" in result


# --- #17: `|` inside a pipe-table cell's code span or math -----------------


# Rows from a real research document. Pandoc's `markdown` reader parses a cell's
# inlines before it looks for the next `|`, so a bar inside a code span or `$...$`
# math is cell content, not a cell boundary: every row here has two cells.
BARS_IN_SPANS_TABLE = (
    "| construct | status |\n"
    "| --- | --- |\n"
    "| closed forms via explicit `|X(F_{q^r})|` for `A^n` | proposed |\n"
    "| `Ann_R(x) = {r∈R | r·x=0} ⊲ R` | proposed |\n"
    "| $|-2K_{\\widetilde V}|=\\{C\\}$ generically | established |\n"
    "| `Tr(Frob^r \\| H)` and $\\int_M \\|F_A\\|^2$ | proposed |\n"
    "| an escaped a \\| b outside spans | proposed |\n"
)


def test_bar_inside_a_code_span_or_math_stays_in_its_cell() -> None:
    """
    Splitting on every bar cut each row at the span and dropped the overflow
    cells, so the cell text after the span was lost; unescaping every `\\|` turned
    TeX's norm `\\|F\\|` into `|F|`. The default verify gate runs here, so this also
    asserts that pandoc reads the same table before and after.
    """
    assert reformat_text(BARS_IN_SPANS_TABLE) == BARS_IN_SPANS_TABLE


@pytest.mark.parametrize("marker", ["(1)", "a.", "a)", "(a)", "#.", "(@)", "A)"])
def test_wrapping_never_starts_a_line_with_a_pandoc_list_marker(marker: str) -> None:
    """
    Inside a list item, pandoc's `fancy_lists` and `example_lists` start a nested
    list at any of these markers at the start of a line. A wrap that lands one
    there changes the document, so the default verify gate would refuse it.
    """
    source = f"- Transport step (4) is fully general; step {marker} is next.\n"

    result = reformat_text(source, width=45, semantic=False)

    assert result.count("\n") == 2, result


@pytest.mark.parametrize("width", [0, 88])
def test_a_sentence_break_never_starts_a_line_with_a_list_marker(width: int) -> None:
    """
    Semantic line breaks put each sentence on its own line, so a sentence that
    begins with a marker-like word (`A)` after `App.`) starts a line too.
    """
    source = (
        "- Truncation bounds via majorants (Thms. 3.4-3.7, App. A) give interval "
        "enclosures of the solutions.\n"
    )

    reformat_text(source, semantic=True, width=width)


def test_bars_only_inside_math_do_not_start_a_table() -> None:
    """
    A pipe-table row needs a `|` outside code and math, and a delimiter cell is
    only `:?-+:?`. This paragraph and the bullet under it are not a table.
    """
    source = (
        "- Item:\n\n"
        "  With grading $|a|' = |a| - 1$, the bracket satisfies:\n"
        "  - Graded skew-symmetry.\n"
    )
    result = reformat_text(source)

    assert "---" not in result
    assert "- Graded skew-symmetry." in result


def test_row_wider_than_its_header_keeps_its_text() -> None:
    """
    A bare `d|N` splits the row, and pandoc drops the cells past the header's
    width. Its reading is the same whatever flowmark writes there, so the gate is
    blind to those cells: writing them back is what keeps their text in the file.
    """
    header = "| lead | capability |\n| --- | --- |\n"
    source = (
        header + "| Lambert series | b_N = \\sum_{d|N} a_d and Mobius inversion |\n"
    )
    # Every cell boundary is written padded, the accidental one included.
    written = (
        header + "| Lambert series | b_N = \\sum_{d | N} a_d and Mobius inversion |\n"
    )

    assert reformat_text(source) == written


def test_a_multiline_html_comment_block_is_kept_verbatim() -> None:
    """
    Pandoc reads an HTML comment at the start of a block, through its `-->`, as one
    `RawBlock` whose text includes the line breaks, so reflowing it changes that
    text. The README's generated-file banner is this shape (#41).
    """
    source = (
        "<!-- Generated from a file via\nscripts/gen.py.\n-->\n\n# Title\n\nText.\n"
    )

    result = reformat_text(source, semantic=False, verify=True)

    assert result.startswith("<!-- Generated from a file via\nscripts/gen.py.\n-->\n")


def test_a_table_in_an_ordered_list_item_stays_in_the_item() -> None:
    """A pipe table indented under an ordered-list item keeps its indentation (#42)."""
    source = (
        "1. First step.\n\n"
        "2. Second step, with a table:\n\n"
        "   | A | B |\n   | --- | --- |\n   | 1 | 2 |\n\n"
        "3. Third step.\n"
    )

    assert reformat_text(source, semantic=True, verify=True) == source


def test_backslash_paren_math_is_opaque() -> None:
    """
    Pandoc's `tex_math_single_backslash` reads `\\(...\\)` as math, so underscores
    inside it are TeX, never emphasis (#43).
    """
    math = r"\(O_{\mathrm{sn}_{K'}}(L)=O(L)\cap\ker\mathrm{sn}_{K'}\)"
    source = f"- Return {math} for the spinor norm.\n"

    assert math in reformat_text(source, semantic=True, verify=True)

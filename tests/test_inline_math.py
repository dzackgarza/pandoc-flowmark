"""Test inline math preservation."""

from dataclasses import replace

import pytest

from flowmark import Width
from flowmark.formats.flowmark_markdown import flowmark_markdown
from flowmark.linewrapping.markdown_filling import fill_markdown
from flowmark.preflight import MalformedInputError
from flowmark.reformat_api import REFORMAT_DEFAULTS, reformat_text


def test_inline_math_preserves_latex_subscripts_verbatim() -> None:
    """Inline math must not parse LaTeX underscores as Markdown emphasis."""
    md = flowmark_markdown()

    source = (
        "The generated semifan is "
        "$\\mathcal{F}_{\\mathrm{gen}} = \\bigcup_{h \\in H} h\\mathcal{F}$.\n"
        "The local forms are $L_{1, \\mathbf{Z}_p}$ and $L_{2, \\mathbf{Z}_p}$.\n"
        "The automorphism group is "
        "$Aut_{\\operatorname{Lat}}(L)$ and $G_\\beta \\subset GL_n$.\n"
    )

    result = md(source)

    assert result == source


def test_same_line_double_dollar_math_preserves_latex_subscripts_verbatim() -> None:
    """Same-line $$...$$ math must not parse LaTeX underscores as Markdown emphasis."""
    md = flowmark_markdown()

    source = "The characterization is $$ \\operatorname{GL}_n = G_\\beta $$.\n"

    result = md(source)

    assert result == source


# --- #17 part 2: an inline math span is atomic to the wrapper ----------------
#
# Wrapping could place a newline at a space *inside* `$...$`, which changes the
# math pandoc reads: `Math InlineMath "H^1(X,\\mathcal O_X)=0"` became
# `Math InlineMath "H^1(X,\\mathcal\nO_X)=0"`. LaTeX tolerates the newline, but
# the AST changes, so `--verify` correctly refused to write -- meaning any
# document with enough inline math was unformattable at the default width. The
# reporter's document hit this ~14 times.
#
# The span is only split when moving it wholesale would leave the previous line
# short, so this is a wrap-cost decision: "inside math" has to cost infinity.

MATH_WRAP_SOURCE = "word word word word word word word word word word word word word and $H^1(X,\\mathcal O_X)=0$ plus more trailing words here to force a wrap decision.\n"


def test_wrapping_never_breaks_inside_inline_math() -> None:
    """The #17 reproducer, at the default width."""
    result = fill_markdown(MATH_WRAP_SOURCE)

    assert "$H^1(X,\\mathcal O_X)=0$" in result, result
    for line in result.splitlines():
        assert line.count("$") % 2 == 0, f"a math span straddles a line break: {line!r}"


def test_wrapping_takes_a_short_line_rather_than_splitting_math() -> None:
    """
    Moving the whole span down is the correct trade, even when it leaves the
    previous line well short of the width. #17 notes flowmark already does the
    right thing when fewer leading words precede the span; the defect was only in
    the case where the cost function preferred the split.
    """
    result = fill_markdown(MATH_WRAP_SOURCE)
    first_line = result.splitlines()[0]

    assert "$" not in first_line, first_line


def test_verify_accepts_the_math_reproducer_at_the_default_width() -> None:
    """
    The user-facing consequence: the document formats with the gate on, rather
    than being unformattable.
    """
    reformat_text(MATH_WRAP_SOURCE)


# --- #28: what is unbreakable comes from the parse, and the parse follows pandoc ---
#
# Pandoc's `tex_math_dollars` (pandoc manual, "Math"): a single-`$` span needs a
# non-space character just inside both delimiters, the closer must not be followed
# by a digit, and the span may continue across a line break inside its paragraph.
# `$$...$$` allows the spaces.


def test_inline_math_across_a_line_break_formats_with_verify() -> None:
    """
    Pandoc reads `$a_1 +\\nb_1 = c$` as one `InlineMath`. Joining the line puts a
    space where the newline was, which TeX reads identically.
    """
    result = reformat_text("Some text with $a_1 +\nb_1 = c$ and more text here.\n")

    assert "$a_1 + b_1 = c$" in result, result


@pytest.mark.parametrize(
    "source",
    ["A $ a _b_ c $ d.\n", "A $x _y_ z$1 d.\n", "First line $a +\nb $ here.\n"],
    ids=["space-padded", "digit-after-closer", "padded-across-lines"],
)
def test_math_pandoc_rejects_is_refused_not_reformatted(source: str) -> None:
    """
    Each is meant as math, but pandoc reads it as text (with `_b_` as emphasis).
    That is an error in the document: flowmark refuses to write it and names the
    line, rather than format the intended TeX as prose.
    """
    with pytest.raises(MalformedInputError):
        reformat_text(source)


def test_prices_are_not_malformed_math() -> None:
    """A `$` before a digit is currency, and two prices are not a padded math span."""
    source = "It costs $5 and $10, and they would be paying $ later.\n"

    assert reformat_text(source) == source


RAW_TEX_WRAP_SOURCE = "The compactification of the moduli space is written as the closure \\overline{ \\mathcal{M}_{1} } in the literature.\n"


def test_wrapping_never_breaks_inside_a_raw_tex_command() -> None:
    """
    Pandoc reads `\\overline{ \\mathcal{M}_{1} }` as one `RawInline tex`, so a break
    at one of its spaces changes that string. At width 78 the command straddles the
    limit: no regex describes it, only the parser knows it is one construct.
    """
    result = reformat_text(RAW_TEX_WRAP_SOURCE, replace(REFORMAT_DEFAULTS, wrap=Width(78)))

    assert "\\overline{ \\mathcal{M}_{1} }" in result, result

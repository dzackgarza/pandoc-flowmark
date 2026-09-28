"""
Test Pandoc fenced div handling (``::: {.attrs}`` ... ``:::``).

Pandoc's grammar for the opening fence is: three or more colons, then the
whole remainder of the line is the div's attribute specification -- either a
braced block (``{#id .class key="val"}``) or a bare class word (``::: proof``).
There is no "trailing content on the same line": pandoc rejects
``::: {.foo} text`` as a div entirely, so no part of the opening line may ever
be re-emitted as div body content.

https://pandoc.org/MANUAL.html#divs-and-spans
"""

from flowmark import FormatOptions, Semantic
from flowmark.formats.flowmark_markdown import flowmark_markdown
from flowmark.linewrapping.markdown_filling import fill_markdown


def test_fenced_div_braced_attrs_round_trip() -> None:
    """A canonical braced attribute block is preserved verbatim."""
    md = flowmark_markdown()

    source = "::: {.foo}\nBody.\n:::\n"

    assert md(source) == source


def test_fenced_div_brace_in_quoted_value_stays_on_opening_fence() -> None:
    """
    A ``}`` inside a quoted attribute value must not terminate the attribute
    block.  Regression: the attribute regex used ``\\{[^}]*\\}``, which stopped
    at the first ``}`` -- including one inside ``title="{...}"`` -- and pushed
    the remainder onto the next line, corrupting the title in the parsed AST.
    """
    md = flowmark_markdown()

    source = '::: {#thm:main-identification .theorem title="{[@AEGS23, Thm. 1.1]}"}\nLet $F$ be the moduli space.\n:::\n'

    assert md(source) == source


def test_fenced_div_brace_mid_quoted_value_stays_on_opening_fence() -> None:
    """A ``}`` in the middle of a quoted value must not split the fence line."""
    md = flowmark_markdown()

    source = '::: {#t .theorem title="a}b"}\nBody.\n:::\n'

    assert md(source) == source


def test_fenced_div_escaped_quotes_in_value_stay_on_opening_fence() -> None:
    """A quoted value may contain escaped quotes around a brace."""
    md = flowmark_markdown()

    source = '::: {#t data="{\\"k\\": 1}"}\nBody.\n:::\n'

    assert md(source) == source


def test_fenced_div_bare_class_round_trip() -> None:
    """
    ``::: proof`` is pandoc's bare-class shorthand for ``::: {.proof}``.
    Regression: the bare word was treated as trailing content and emitted as
    body text, which stopped the block from parsing as a Div at all.
    """
    md = flowmark_markdown()

    source = "::: proof\nBody.\n:::\n"

    assert md(source) == source


def test_fenced_div_long_fence_round_trip() -> None:
    """Pandoc allows any opening fence of three or more colons."""
    md = flowmark_markdown()

    source = ":::::::: proof\nBody.\n::::::::\n"

    assert md(source) == source


def test_fenced_div_long_fence_with_short_closer_round_trip() -> None:
    """Pandoc accepts a closing fence shorter than the opening fence."""
    md = flowmark_markdown()

    source = ":::::::::::::::: proof\nBody.\n:::\n"

    assert md(source) == source


def test_fenced_div_no_attrs_round_trip() -> None:
    """A bare ``:::`` fence with no attribute spec is preserved."""
    md = flowmark_markdown()

    source = ":::\nBody.\n:::\n"

    assert md(source) == source


def test_fenced_div_attr_block_without_space_is_normalized() -> None:
    """
    ``:::{.foo}`` is normalized to pandoc's canonical ``::: {.foo}`` spacing.
    This is a rendering choice, not a parse change: both forms carry the same
    attributes, so the parsed AST is unaffected.
    """
    md = flowmark_markdown()

    assert md(":::{.foo}\nBody.\n:::\n") == "::: {.foo}\nBody.\n:::\n"


def test_fenced_div_opening_fence_never_leaks_into_the_body() -> None:
    """
    Nothing on the opening fence line may appear as body content.

    This was previously spelled as "the body is preserved verbatim", which
    conflated two separate guarantees: that the *fence* is not re-read as content
    (still true, and what this asserts) and that the *body* is opaque (false --
    see #20 and `test_fenced_div_body_reflows_like_any_other_markdown`).
    """
    md = flowmark_markdown()

    source = "::: {.foo}\nBody.\n:::\n"

    assert md(source) == source


# --- #20: div bodies are ordinary markdown -----------------------------------
#
# A pandoc div's body is parsed as ordinary blocks -- `pandoc -f markdown -t
# native` on `::: {.problem}\ntext\n:::` gives `Div [Para [...]]`, with normal
# inlines. The fence is a semantic wrapper, not a content mode. That makes it
# unlike its verbatim-capture neighbours (display math, raw TeX, `\begin{env}`),
# whose bodies are genuinely not markdown.
#
# On the document behind #17/#19/#20 this was 108 div blocks and 693 lines -- ~24%
# of the file -- silently passed through with their original column-88 wrapping,
# while the run reported success and `--verify` passed (correctly: no meaning
# changed). These are `{.problem}`/`{.theorem}` environments, the mathematically
# dense sections where sentence-granular diffs matter most.

DIV_PARAGRAPH = "The first sentence states a fact. The second sentence states another fact entirely.\n"


def test_fenced_div_body_reflows_like_any_other_markdown() -> None:
    """
    The #20 reproducer pair: the same paragraph must reflow the same way whether
    or not it is wrapped in a div.
    """
    bare = fill_markdown(DIV_PARAGRAPH, FormatOptions(Semantic()))
    wrapped = fill_markdown(f"::: {{.problem}}\n{DIV_PARAGRAPH}:::\n", FormatOptions(Semantic()))

    assert bare == "The first sentence states a fact.\nThe second sentence states another fact entirely.\n"
    assert wrapped == f"::: {{.problem}}\n{bare}:::\n"


def test_fenced_div_body_keeps_block_structure() -> None:
    """
    A div body may hold any block a document may hold. Each must survive as
    itself -- a list stays a list, a fenced code block keeps its fence and its
    contents untouched, display math stays verbatim, and a nested div nests.
    """
    md = flowmark_markdown()

    source = "::: {.theorem}\nIntro paragraph.\n\n- first item\n\n- second item\n\n```python\nx  =  1\n```\n\n$$\na  +  b\n$$\n\n::: {.proof}\nInner body.\n:::\n:::\n"

    assert md(source) == source


def test_fenced_div_body_may_define_a_footnote() -> None:
    """
    A footnote *definition* inside a div must round-trip (#34).

    The definition and its reference both sit inside the div, and the div's
    source is left byte-identical.
    """
    md = flowmark_markdown()

    source = "::: {.solution}\nThe argument is due to Oskar.[^credit]\n\n[^credit]: With thanks.\n:::\n"

    assert md(source) == source

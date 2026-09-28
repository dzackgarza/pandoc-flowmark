import pytest

from flowmark.formats.flowmark_markdown import flowmark_markdown
from flowmark.linewrapping.line_wrappers import line_wrap_by_sentence
from flowmark.linewrapping.markdown_filling import fill_markdown
from flowmark.pandoc_verify import (
    _SUSPENSION_WORDS as _VERIFY_SUSPENSION,  # pyright: ignore[reportPrivateUsage]
)
from flowmark.pandoc_verify import MeaningChangedError, check_meaning_preserved
from flowmark.reformat_api import reformat_text
from flowmark.transforms.doc_cleanups import (
    _SUSPENSION_WORDS as _CLEANUP_SUSPENSION,  # pyright: ignore[reportPrivateUsage]
)
from flowmark.transforms.doc_cleanups import unbold_headings

input_md = """
# **Bold Heading 1**

Some paragraph text.

## ***Bold Italic***

## **Simple Bold**

### Not Bold

#### **Partial** Bold

#### Other *partial* **bold** `code`

- **List Item Bold**

Another paragraph with **bold** text.

## **Nested `code`**

Final text.
"""


expected_md = """
# Bold Heading 1

Some paragraph text.

## *Bold Italic*

## Simple Bold

### Not Bold

#### **Partial** Bold

#### Other *partial* **bold** `code`

- **List Item Bold**

Another paragraph with **bold** text.

## Nested `code`

Final text.
"""


def test_unbold_headings() -> None:
    marko = flowmark_markdown(line_wrap_by_sentence())

    doc = marko.parse(input_md)
    unbold_headings(doc)
    rendered_md = marko.render(doc).strip()

    assert rendered_md == expected_md.strip()


# --- #18: a line break that fell after a hyphen joins without a space --------
#
# Hard-wrapping tools break lines wherever they find an opportunity, and many
# treat a hyphen as one. Markdown has no soft-hyphen semantics: a line break
# inside a paragraph *is* whitespace, so a break placed after a hyphen silently
# inserts a space into the word. `degree-\n2` renders as `degree- 2`, and the
# damage is already in the rendered output before any formatter runs.
#
# This is squarely flowmark's job: it is the tool that unwraps and rewraps, and
# so the only point in the pipeline that sees the join happen. Reflowing today
# preserves the space, because it is a faithful `SoftBreak` -- correct per the
# AST, and exactly the class of "common issue" `--cleanups` exists for.

# The seven sites from #18's table, verbatim: source, and the wanted join.
HYPHEN_JOIN_SITES = [
    ("the degree-\n2 Coble locus", "degree-2 Coble locus"),
    ("a recognizable-\ndivisor here", "recognizable-divisor"),
    ("the **semi-log-\ncanonical** case", "**semi-log-canonical**"),
    ("the degree-\n$4$ class", "degree-$4$"),
    ("the degree-\n$2$ congruence", "degree-$2$"),
    ("the white-\nroot wall", "white-root"),
    ("a fan-versus-\npolytope map", "fan-versus-polytope"),
]


@pytest.mark.parametrize(("source", "wanted"), HYPHEN_JOIN_SITES)
def test_hyphen_join_drops_the_space_at_a_line_break(source: str, wanted: str) -> None:
    """
    Every site in #18's table produces its "wanted" column.

    Two of these are why the rule cannot live at the `Str` level: the break can
    fall inside inline markup (`**semi-log-` / `canonical**`) and the following
    token can be inline math (`degree-` / `$4$`).
    """
    result = fill_markdown(source + "\n", cleanups=True, dedent_input=False)

    assert wanted in result, result


# Suspended hyphenation is real and legitimate: `the pre- and post-stable models`
# means something, and joining it to `pre-and` corrupts the sentence.
SUSPENSION_WORDS = ["and", "or", "to", "nor", "but", "through", "versus"]


@pytest.mark.parametrize("word", SUSPENSION_WORDS)
def test_suspended_hyphenation_is_never_joined(word: str) -> None:
    """One test per member of the suspension scope."""
    result = fill_markdown(
        f"the pre-\n{word} post-stable models\n", cleanups=True, dedent_input=False
    )

    assert f"pre- {word}" in result, result


def test_an_authored_space_after_a_hyphen_is_left_alone() -> None:
    """
    The rule fires only at a line join. A `degree- 2` the author typed on one
    line is the author's, and reflowing must not silently rewrite it.
    """
    result = fill_markdown(
        "the degree- 2 Coble locus\n", cleanups=True, dedent_input=False
    )

    assert "degree- 2" in result, result


def test_a_join_beside_an_authored_hyphen_space_passes_verification() -> None:
    """
    One paragraph can hold both: a break after `semi-log-` that the cleanup closes
    up, and a `post- cases` the author typed on one line, which it leaves alone.
    The gate must accept exactly that result, not demand every `- x` be joined.
    """
    result = reformat_text(
        "A semi-log-\nscale plot of the post- cases.\n", cleanups=True, verify=True
    )

    assert "semi-log-scale plot of the post- cases." in result, result


def test_hyphen_join_requires_cleanups() -> None:
    """Without `-c` the faithful `SoftBreak` spacing stands."""
    result = fill_markdown(
        "the degree-\n2 Coble locus\n", cleanups=False, dedent_input=False
    )

    assert "degree- 2" in result, result


def test_hyphen_join_passes_verification() -> None:
    """
    The join deliberately changes the AST (`Str "degree-", Space, Str "2"` becomes
    `Str "degree-2"`), so it needs a declared normalization rather than the gate
    being loosened.
    """
    reformat_text(
        "the degree-\n2 Coble locus and more words\n", cleanups=True, verify=True
    )


def test_hyphen_join_scope_matches_the_cleanup() -> None:
    """
    The gate and the formatter must agree on the suspension scope.

    A word the cleanup joins but the gate refuses is a document that cannot be
    written; a word the gate would accept but the cleanup never produces is dead
    permission. The two lists are separate because they live in separate modules,
    so this is what keeps them one rule.
    """
    assert _CLEANUP_SUSPENSION == _VERIFY_SUSPENSION
    assert set(SUSPENSION_WORDS) == _CLEANUP_SUSPENSION


@pytest.mark.parametrize("word", SUSPENSION_WORDS)
def test_gate_refuses_a_joined_suspension(word: str) -> None:
    """
    The other half of the suspension rule. The cleanup never joins `pre- and`, and
    if something else did, the gate must still catch it: joining a suspended
    hyphen corrupts the sentence.
    """
    with pytest.raises(MeaningChangedError):
        check_meaning_preserved(
            f"the pre-\n{word} post-stable models\n",
            f"the pre-{word} post-stable models\n",
        )


def test_hyphen_join_reports_how_many(capsys: pytest.CaptureFixture[str]) -> None:
    """
    #18 asks for a count rather than silence, because the scope is heuristic and
    will not be right every time. Saying how many is what lets a reader check them.
    """
    fill_markdown(
        "the degree-\n2 locus and the white-\nroot wall\n",
        cleanups=True,
        dedent_input=False,
    )

    assert "closed up 2 line breaks" in capsys.readouterr().err

"""
Tests for the pandoc AST oracle.

The oracle exists so flowmark stops re-deriving pandoc's grammar by hand and
hoping: pandoc owns what these documents mean, so it is the thing to ask. The
contract it enforces is "flowmark may change spelling; it may never change what
pandoc reads."

These are skipped without the pandoc binary, so contributors without it are not
blocked. CI has pandoc and runs them.
"""

from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest

from flowmark import FormatOptions, Pass, Width, reformat_text_unchecked
from flowmark.pandoc_verify import (
    ALERT_TYPE,
    HYPHEN_JOIN,
    LAZY_LIST,
    LIST_SPACING,
    NORMALIZATIONS,
    SMART_QUOTES,
    UNBOLD_HEADING,
    MeaningChangedError,
    PandocUnavailableError,
    check_meaning_preserved,
    pandoc_ast,
)
from flowmark.reformat_api import (
    REFORMAT_DEFAULTS,
    InPlace,
    reformat_file,
    reformat_text,
)
from flowmark.typography.ellipses import ellipses


def test_oracle_reads_house_math_dialect() -> None:
    blocks = pandoc_ast(r"A value \(x + y\) is fixed.")
    assert blocks[0] == {
        "t": "Para",
        "c": [
            {"t": "Str", "c": "A"},
            {"t": "Space"},
            {"t": "Str", "c": "value"},
            {"t": "Space"},
            {"t": "Math", "c": [{"t": "InlineMath"}, "x + y"]},
            {"t": "Space"},
            {"t": "Str", "c": "is"},
            {"t": "Space"},
            {"t": "Str", "c": "fixed."},
        ],
    }


def _no_pandoc(_cmd: str) -> None:
    """Stand-in for `shutil.which` with pandoc absent from PATH."""
    return None


# Each pair is a reproducer and the bytes flowmark actually emitted for it before
# the fix -- captured by running the pre-fix code, not hand-written, so these are
# the real defects rather than a guess at their shape. An oracle that misses any
# of them would not have been worth adding.
PRE_FIX_CORRUPTIONS = [
    pytest.param(
        "::: proof\nBody.\n:::\n",
        ":::\nproof\nBody.\n:::\n",
        id="3-bare-class-div-lost",
    ),
    pytest.param(
        "Text.[^1] More text.[^2]\n\n[^1]: First note.\n[^2]: Second note.\n",
        "Text.[^1] More text.[^2]\n\n[^1]: First note. [^2]: Second note.\n\n",
        id="5-footnote-deleted",
    ),
    pytest.param(
        "::: {.a}\nO.\n\n::: {.b}\nI.\n:::\n\nA.\n:::\n",
        ":::{.a}\nO.\n\n::: {.b}\nI.\n:::\n\nA.\n:::\n:::\n",
        id="6-div-content-escapes",
    ),
    pytest.param(
        "    literal code\n\nAfter.\n",
        "literal code\n\nAfter.\n",
        id="7-code-becomes-prose",
    ),
    pytest.param(
        "A \\overline{ \\mathcal{M}_{1} } b y_{2} c.\n",
        "A \\overline{ \\mathcal{M}*{1} } b y*{2} c.\n",
        id="8-raw-tex-becomes-emphasis",
    ),
    pytest.param(
        "::: {.foo}\n```\n:::\n```\nAfter.\n:::\n",
        "::: {.foo}\n```\n:::\n```\nAfter.\n:::\n```\n",
        id="6-colon-run-in-code-block",
    ),
]


@pytest.mark.parametrize(("source", "corrupted"), PRE_FIX_CORRUPTIONS)
def test_oracle_catches_every_real_meaning_change(source: str, corrupted: str) -> None:
    """
    The oracle must fail on output that changed meaning -- otherwise it proves
    nothing. Two of these (#5, #8) differ from their source only *within* a
    block, which is what pins the inline whitespace normalization: it must not
    be loose enough to let a deleted footnote or a mangled TeX command through.
    """
    with pytest.raises(MeaningChangedError):
        check_meaning_preserved(source, corrupted)


class NormalizationContract(NamedTuple):
    """
    The proof one `NORMALIZATIONS` entry owes, per the contract documented on
    `NORMALIZATIONS` itself.

    `positive` is a source/result pair the entry must accept and be credited for.
    `negative` is a *nearby* pair -- the same construct, the same shape -- that must
    still raise. The negative case is the load-bearing half: it is what proves the
    entry carved out one opinion rather than widening the gate around a whole
    construct. A positive case alone would be satisfied by an entry that accepts
    everything.
    """

    key: str
    positive: tuple[str, str]
    negative: tuple[str, str]
    negative_reason: str


NORMALIZATION_CONTRACT: tuple[NormalizationContract, ...] = (
    NormalizationContract(
        key=UNBOLD_HEADING,
        positive=("# **X**\n", "# X\n"),
        negative=("# **X**\n", "# *X*\n"),
        negative_reason=("the heading's bold became emphasis rather than being dropped, so the document gained markup flowmark never claims to add"),
    ),
    NormalizationContract(
        key=LIST_SPACING,
        positive=("- a\n- b\n", "- a\n\n- b\n"),
        negative=("- a\n- b\n", "- a\n\n- b\n\n- c\n"),
        negative_reason="an item appeared; spacing is spelling, an extra item is not",
    ),
    NormalizationContract(
        key=SMART_QUOTES,
        positive=('He said "hi" and there.\n', "He said “hi” and there.\n"),
        negative=('He said "hi" and there.\n', "He said hi and there.\n"),
        negative_reason=("the quotation marks were dropped rather than curled; the entry writes the marks into the text precisely so a lost or moved quote still shows"),
    ),
    NormalizationContract(
        key=ALERT_TYPE,
        positive=("> [!note]\n> Text.\n", "> [!NOTE]\n> Text.\n"),
        negative=("> [!note]\n> Text.\n", "> [!NOTED]\n> Text.\n"),
        negative_reason=("the alert type changed rather than its case: NOTED is no GFM alert"),
    ),
    NormalizationContract(
        key=LAZY_LIST,
        positive=(
            "Shared infrastructure:\n- a\n- b\n",
            "Shared infrastructure:\n\n- a\n\n- b\n",
        ),
        negative=(
            "Shared infrastructure:\n\n- a\n\n- b\n",
            "Shared infrastructure: - a - b\n",
        ),
        negative_reason=(
            "the exact reverse: a real list flattened into prose. The entry rewrites "
            "only the side that gained the list, so the side that lost one is never "
            "rewritten and never reconciles -- which is the whole reason a "
            "normalization sees both trees instead of one node"
        ),
    ),
    NormalizationContract(
        key=HYPHEN_JOIN,
        positive=("the degree-\n2 Coble locus\n", "the degree-2 Coble locus\n"),
        negative=("the degree-2 Coble locus\n", "the degree- 2 Coble locus\n"),
        negative_reason=(
            "the exact reverse: a hyphenated word came apart. That is not a "
            "hypothetical corruption -- it is the damage #18 says wrapping tools "
            "inflict, and the reason the cleanup exists, so the gate must keep "
            "catching it"
        ),
    ),
)


def test_every_normalization_declares_its_contract() -> None:
    """
    The table above must cover `NORMALIZATIONS` exactly.

    This is what makes the contract enforceable rather than aspirational: a new
    entry cannot be added without a positive case and a nearby negative one, and
    an entry cannot be quietly removed while its proof lingers.
    """
    declared = [key for key, _text, _normalize in NORMALIZATIONS]
    proven = [contract.key for contract in NORMALIZATION_CONTRACT]
    assert sorted(proven) == sorted(declared)
    assert len(proven) == len(set(proven)), "an entry is listed twice"


@pytest.mark.parametrize("contract", NORMALIZATION_CONTRACT, ids=lambda c: f"{c.key}-accepts")
def test_normalization_accepts_its_positive_case(
    contract: NormalizationContract,
) -> None:
    """
    Flowmark's opinionated normalizations do change pandoc's AST, so full AST
    equality is not the contract. A heading's weight belongs to the `<h1>` or
    `\\section`, not to hand-applied bold, and list spacing is standardized.
    These must pass -- and be reported, since they are real AST changes.

    Attribution is asserted exactly: the entry must be credited for its own case
    and no other entry may be, which is what keeps one carve-out from being
    reported as another.
    """
    source, result = contract.positive
    assert check_meaning_preserved(source, result) == [contract.key]


@pytest.mark.parametrize("contract", NORMALIZATION_CONTRACT, ids=lambda c: f"{c.key}-still-refuses")
def test_normalization_still_refuses_its_negative_case(
    contract: NormalizationContract,
) -> None:
    """
    The gate did not widen: a corruption of the same shape as the declared
    opinion is still refused.
    """
    source, result = contract.negative
    with pytest.raises(MeaningChangedError):
        check_meaning_preserved(source, result)


# The #16 reproducer, verbatim: three lines, no blank line before the bullets.
# CommonMark starts a list at the `-`; pandoc's `markdown` dialect swallows it as
# lazy continuation of the open paragraph. Ordinary "intro sentence, then bullets"
# markdown, and unformattable today.
LAZY_LIST_SOURCE = "Shared infrastructure:\n- Polynomial reduction backends\n- Modular reconstruction\n"


def test_paragraph_then_tight_list_is_formattable() -> None:
    """
    The list flowmark materializes is what the author plainly meant, so the gate
    must accept it rather than refusing the document outright.

    Following pandoc instead -- reflowing the bullets back into prose -- was
    rejected: it destroys a list the author drew and that every CommonMark reader,
    GitHub included, renders as a list.
    """
    reformat_text(LAZY_LIST_SOURCE)


def test_paragraph_then_nested_tight_list_is_formattable() -> None:
    """
    An indented sub-bullet under a lazy line is more of the same paragraph to
    pandoc, so the list flowmark materializes may nest.
    """
    source = "**2. Fundamental matrix**\n* Hard: `phi1..4` for this GKZ\n* General: enclose the matrix by a truncated sum.\n  * Needs: a coefficient growth estimate.\n"
    reformat_text(source)


def test_paragraph_then_tight_list_writes_the_file(tmp_path: Path) -> None:
    """
    The acceptance criterion as the reporter stated it: the file is written, not
    merely accepted in-process.
    """
    doc = tmp_path / "min.md"
    doc.write_text(LAZY_LIST_SOURCE)

    reformat_file(doc, InPlace(""), REFORMAT_DEFAULTS)

    written = doc.read_text()
    assert written != LAZY_LIST_SOURCE
    assert "\n\n- Polynomial reduction backends" in written


# A table of contents as markdown-toc tooling writes it: the closing marker is on
# the line right after the last item. Flowmark's reader, like CommonMark, reads the
# marker as an HTML block after the list, and flowmark sets it off with a blank
# line so readers without that rule agree.
TOC_SOURCE = "<!--toc:start-->\n- [Intake](#intake)\n  - [Leads](#leads)\n<!--toc:end-->\n"


def test_toc_end_marker_after_a_list_is_formattable() -> None:
    result = reformat_text(TOC_SOURCE)

    assert result.endswith("- [Leads](#leads)\n\n<!--toc:end-->\n")


# Markdoc tags wrapped around a tight list, with no blank lines. Flowmark's reader
# reads a tag, a tight list, and a tag; flowmark writes them apart, list loose.
MARKDOC_WRAPPED_LIST = "{% field %}\n- a\n- b\n{% /field %}\n"


def test_markdoc_tags_around_a_tight_list_verify() -> None:
    result = reformat_text(MARKDOC_WRAPPED_LIST)

    assert result == "{% field %}\n\n- a\n\n- b\n\n{% /field %}\n"
    assert set(check_meaning_preserved(MARKDOC_WRAPPED_LIST, result)) == {LIST_SPACING}


def test_prose_in_place_of_the_closing_tag_still_raises() -> None:
    with pytest.raises(MeaningChangedError):
        check_meaning_preserved("{% field %}\n- a\ntext\n", "{% field %}\n\n- a\n\ntext\n")


def _many_block_document(count: int = 40) -> list[str]:
    return [f"Paragraph number {i} with enough words in it to be realistic." for i in range(count)]


def test_mismatch_names_the_differing_block_rather_than_dumping_the_ast() -> None:
    """
    A mismatch in a mid-size document must not emit its entire block list.

    flowmark is wired into a `pre-commit`/`pre-push` gate, where a kilobyte of AST
    per failing file buries every other finding in the run. One real file produced
    a 2053-character warning. The block index and the two types are what a reader
    needs; the rest was noise.
    """
    blocks = _many_block_document()
    corrupted = list(blocks)
    corrupted[7] = "# " + blocks[7]

    with pytest.raises(MeaningChangedError) as excinfo:
        check_meaning_preserved("\n\n".join(blocks) + "\n", "\n\n".join(corrupted) + "\n")

    message = str(excinfo.value)
    assert "block 7" in message
    assert "Para" in message
    assert "Header" in message
    assert len(message) < 400, f"message is {len(message)} characters:\n{message}"


def test_mismatch_names_the_block_that_actually_blocks_acceptance() -> None:
    """
    A block reconcilable by a declared normalization must not be reported as the
    problem.

    Found while formatting `tests/tryscript/fixtures/content/typography.md`: the
    message pointed at `He said "hello" to her.`, which `smart_quotes` accepts
    outright, while the block that genuinely defeated reconciliation was further
    down the document. Sending a reader to a block that is fine costs exactly the
    bisection this diagnostic exists to prevent.
    """
    source = '# **X**\n\nUntouched paragraph.\n\nHe said "hi" and there.\n'
    result = "# X\n\nUntouched paragraph.\n\nHe said hi and there.\n"

    with pytest.raises(MeaningChangedError) as excinfo:
        check_meaning_preserved(source, result)

    message = str(excinfo.value)
    assert "block 2" in message, message
    assert "block 0" not in message, "the unbolded heading is accepted, not the blocker"


def test_mismatch_names_the_block_when_only_content_differs() -> None:
    """
    Equal block types are the harder case: the old message could say nothing but
    "same block types, altered content", leaving the reader to diff two documents
    by hand. The index alone turns that into a lookup.
    """
    blocks = _many_block_document()
    corrupted = list(blocks)
    corrupted[12] = blocks[12].replace("realistic", "realistic and different")

    with pytest.raises(MeaningChangedError) as excinfo:
        check_meaning_preserved("\n\n".join(blocks) + "\n", "\n\n".join(corrupted) + "\n")

    assert "block 12" in str(excinfo.value)


@pytest.mark.parametrize(
    ("source", "result"),
    [
        # Near-misses of the unbold carve-out: the gate must not have widened into
        # "any lost Strong is fine".
        pytest.param("**b** x\n", "b x\n", id="bold-lost-in-a-paragraph"),
        pytest.param("# **X Y**\n", "# X\n", id="heading-loses-bold-and-a-word"),
        pytest.param("# *X*\n", "# X\n", id="emph-not-bold-lost-in-heading"),
        pytest.param("# X\n", "## X\n", id="heading-level-changed"),
        # A real live defect (#11) must still be caught.
        pytest.param("H~2~O\n", "H~~2~~O\n", id="subscript-becomes-strikeout"),
    ],
)
def test_changes_beyond_the_normalizations_still_fail(source: str, result: str) -> None:
    """The normalizations are carve-outs, not a general amnesty for lost markup."""
    with pytest.raises(MeaningChangedError):
        check_meaning_preserved(source, result)


def test_oracle_accepts_a_deliberate_spelling_change() -> None:
    """
    Flowmark rewrites an indented code block to a fenced one. Different bytes,
    same `CodeBlock` -- the oracle must not object.
    """
    check_meaning_preserved("    literal code\n", "```\nliteral code\n```\n")


def test_oracle_accepts_rewrapped_prose() -> None:
    """Rewrapping is flowmark's whole job; pandoc reads the same Para either way."""
    source = "One sentence here. Another sentence there.\n"
    rewrapped = "One sentence here.\nAnother sentence there.\n"

    check_meaning_preserved(source, rewrapped)


@pytest.mark.parametrize(
    "source",
    [
        # #3: fenced div attribute specs.
        '::: {#thm .theorem title="{[@AEGS23, Thm. 1.1]}"}\nBody.\n:::\n',
        "::: proof\nBody.\n:::\n",
        ":::::::: proof\nBody.\n::::::::\n",
        # #5: footnote definitions.
        "Text.[^1] More.[^2]\n\n[^1]: First.\n[^2]: Second.\n",
        "T.[^1]\n\n[^1]:\n    First para.\n\n    Second para.\n",
        # #6: nested divs, including colon runs inside code blocks.
        "::: {.theorem}\nOuter.\n\n::: {.proof}\nInner.\n:::\n\nAfter.\n:::\n",
        "::: {.foo}\n```\n:::\n```\nAfter.\n:::\n",
        "::: {.foo}\n```\n::: {.bar}\n```\nAfter.\n:::\n",
        "::: {.foo}\n~~~\n:::\n~~~\nAfter.\n:::\n",
        # #7: indented code blocks.
        "    literal code\n\nAfter.\n",
        # #8: raw inline TeX.
        "A \\overline{ \\mathcal{M}_{1} } b y_{2} c.\n",
        "A $\\overline{ \\mathcal{M}_{1} }$ b $y_{2}$ c.\n",
    ],
)
def test_reformatting_preserves_meaning(source: str) -> None:
    """
    Every construct family in PR #4, checked against pandoc rather than against
    an expected string a human guessed at.
    """
    reformat_text(source)


def test_smartquotes_passes_verification() -> None:
    """
    `smartquotes` is invisible to pandoc for free -- its `smart` extension folds
    straight and curly quotes alike into `Quoted`.
    """
    reformat_text(
        'He said "hi" and there.\n',
        replace(REFORMAT_DEFAULTS, passes=frozenset({Pass.cleanups, Pass.smartquotes})),
    )


@pytest.mark.parametrize(
    "source",
    [
        "He said... yes\n",  # space already after: the easy case
        "word...word\n",  # ellipses inserts a space on BOTH sides
        "a...b and c... d\n",
        "Wait...\n",
    ],
)
def test_ellipsis_spacing_is_not_a_meaning_change(source: str) -> None:
    """
    `ellipses` respells text (`word...word` -> `word … word`) without changing
    what it means, so the oracle must stay quiet for it.

    The expected output comes from `ellipses()` itself rather than a literal, so
    this is pinned to the contract's owner: if `typography/ellipses.py` changes
    its spacing rule, this test picks the new rule up and fails here if the
    oracle cannot tolerate it -- instead of the two drifting apart in silence.
    """
    check_meaning_preserved(source, ellipses(source))
    reformat_text(
        source,
        replace(REFORMAT_DEFAULTS, passes=frozenset({Pass.cleanups, Pass.ellipses})),
    )


def test_verify_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The gate is a safety default: formatting must not proceed unverified unless
    asked. The stand-in gate refuses every change, so formatting a document that
    changes raises only if the gate runs by default.
    """

    def refuse(_source: str, _result: str, label: str = "input") -> None:
        raise MeaningChangedError(f"simulated meaning change in {label}")

    monkeypatch.setattr("flowmark.reformat_api.check_meaning_preserved", refuse)

    with pytest.raises(MeaningChangedError):
        reformat_text("Hi   there.\n")


def test_no_verify_skips_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-verify` must actually bypass the check, not merely be accepted."""

    def refuse(_source: str, _result: str, label: str = "input") -> None:
        raise MeaningChangedError(f"simulated meaning change in {label}")

    monkeypatch.setattr("flowmark.reformat_api.check_meaning_preserved", refuse)

    assert reformat_text_unchecked("Hi   there.\n") == "Hi there.\n"


def test_missing_pandoc_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A missing binary must raise, not silently skip the check. A verification that
    quietly passes when it cannot run is worse than none.
    """
    monkeypatch.setattr("flowmark.pandoc_reader.shutil.which", _no_pandoc)

    with pytest.raises(PandocUnavailableError, match="pandoc"):
        reformat_text("Sentence one is here. Sentence two follows it. Sentence three ends the\nparagraph now, quite long indeed, wrapping past width.\n")


def test_a_destructive_change_leaves_the_file_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The whole point of the gate: a document flowmark would damage keeps its
    original bytes.

    The gate is forced to fire rather than fed a document that currently trips it
    (#10, #11): those are live bugs due to be fixed, and a test pinned to one
    would start failing the day it is. What is under test here is the plumbing --
    that a firing gate prevents the write -- not the detection, which
    `test_oracle_catches_every_real_meaning_change` covers against real defects.
    """
    doc = tmp_path / "doc.md"
    original = "# Title\n\nsome   text   here\n"
    doc.write_text(original)

    def refuse(_source: str, _result: str, label: str = "input") -> None:
        raise MeaningChangedError(f"simulated meaning change in {label}")

    monkeypatch.setattr("flowmark.reformat_api.check_meaning_preserved", refuse)

    with pytest.raises(MeaningChangedError):
        reformat_file(doc, InPlace(""), FormatOptions(Width(), frozenset({Pass.cleanups})))

    assert doc.read_text() == original


def test_preserved_construct_is_not_reported_as_applied() -> None:
    """A document can *contain* a bold heading that formatting preserves while a
    different normalization genuinely applies. Attribution must name only the
    normalization that reconciled the difference, not every construct present."""
    source = "# **Kept Bold**\n\n- a\n- b\n"
    result = "# **Kept Bold**\n\n- a\n\n- b\n"
    assert check_meaning_preserved(source, result) == [LIST_SPACING]


def test_pandoc_hostile_frontmatter_is_excluded_from_the_oracle() -> None:
    """YAML frontmatter is document metadata, not body, and the formatter preserves
    it verbatim -- so the oracle compares the body only and frontmatter never
    reaches pandoc.

    This matters because real-world frontmatter is often lax YAML that pandoc's
    metadata reader rejects: a Cursor/agent-rule `globs: *.py` value is read as a
    YAML alias (`*`) and aborts the whole parse. Sending it to pandoc would fail
    verification for a document flowmark never intended to touch there.
    """
    fm = "---\ndescription: Rules\nglobs: *.py, pyproject.toml\nalwaysApply: false\n---\n\n"
    # Body meaning is identical (whitespace only); frontmatter is pandoc-hostile.
    assert check_meaning_preserved(fm + "Some   body    text.\n", fm + "Some body text.\n") == []


def test_frontmatter_stripping_does_not_hide_a_body_change() -> None:
    """Excluding frontmatter from the oracle must not blind it to the body: a real
    body meaning change is still caught even when the frontmatter itself is
    pandoc-hostile."""
    fm = "---\nglobs: *.py\n---\n\n"
    with pytest.raises(MeaningChangedError):
        # Paragraph -> heading is a genuine meaning change in the body.
        check_meaning_preserved(fm + "A plain paragraph.\n", fm + "# A plain paragraph.\n")

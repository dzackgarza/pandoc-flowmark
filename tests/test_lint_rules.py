"""Coverage for Flowmark's explicit Pandoc-aware lint rule layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowmark.lint import LintOptions, StyleRule, lint_text


def rule_ids(
    text: str, *, options: LintOptions | None = None, source_path: Path | None = None
) -> set[str]:
    return {
        diagnostic.rule
        for diagnostic in lint_text(text, options, source_path=source_path)
    }


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("# H1\n\n### H3\n", "heading/increment"),
        ("## Same\n\nText\n\n## Same\n", "heading/duplicate"),
        ("# A\n\n# B\n", "heading/multiple-h1"),
        ("#Heading\n", "heading/malformed"),
        ("####### Heading\n", "heading/malformed"),
        ("[text][missing]\n", "reference/undefined"),
        ("[unused]: /target\n", "reference/unused-definition"),
        ("[x]: /a\n[x]: /b\n\n[x][]\n", "reference/duplicate-definition"),
        ("[text]()\n", "link/empty-destination"),
        ("(text)[https://example.com]\n", "link/reversed-syntax"),
        ("[text](https://example.com]\n", "link/malformed-syntax"),
        ("[ text ](https://example.com)\n", "link/text-padding"),
        ("Use * text * here.\n", "emphasis/padding"),
        ("![](image.png)\n", "accessibility/image-alt"),
        ("[click here](https://example.com)\n", "link/non-descriptive-text"),
        ("# Heading\n\n[bad](#missing)\n", "link/invalid-fragment"),
        ("Text[^missing].\n", "footnote/undefined"),
        ("[^unused]: note\n", "footnote/unused-definition"),
        ("Text[^x].\n\n[^x]: one\n[^x]: two\n", "footnote/duplicate-definition"),
        ("---\ntitle: A\ntitle: B\n---\n\nText\n", "frontmatter/duplicate-key"),
        ("---\ntitle: [oops\n---\n\nText\n", "frontmatter/malformed-flow"),
        ("---\ntitle: A\n", "frontmatter/unclosed"),
        ("```\ncode\n```\n", "code/missing-language"),
        ("```python\ncode\n", "code/unclosed-fence"),
        ("```python\n\tx=1\n```\n", "code/hard-tab"),
        ("Text\n```python\nx=1\n```\nAfter\n", "code/surrounding-blank-lines"),
        (
            "Text\n| a | b |\n| --- | --- |\n| x | y |\nAfter\n",
            "table/surrounding-blank-lines",
        ),
        ("# A {#x}\n\n# B {#x}\n", "pandoc/duplicate-identifier"),
        ("# A {#x .foo\n", "pandoc/malformed-attributes"),
        ("::: theorem\nText\n", "pandoc/unclosed-fenced-div"),
        ("\\begin{align}\nx &= y\n", "tex/unclosed-environment"),
        ("\\[\nx^2\n\\]\n", "math/backslash-delimiter"),
        ("Inline \\(x^2\\) math.\n", "math/backslash-delimiter"),
    ],
)
def test_default_rules_cover_common_structural_and_semantic_failures(
    source: str, expected: str
) -> None:
    assert expected in rule_ids(source)


def test_canonical_format_rule_is_suppressed_when_specific_rule_owns_same_line() -> (
    None
):
    diagnostics = lint_text("Text[^x].\n\n[^x]: one\n[^x]: two\n")
    assert "footnote/duplicate-definition" in {d.rule for d in diagnostics}
    assert not any(d.rule == "format/canonical" and d.line == 4 for d in diagnostics)


def test_math_code_and_raw_tex_are_opaque_to_markdown_rules() -> None:
    source = (
        "Math $[x][missing] * text * x_i$, $$y_j [bad](url]$$, "
        "and \\underline{z_k}.\n\n"
        "```text\n#Heading\n[text][missing]\nhttps://example.com\n```\n"
    )
    diagnostics = lint_text(
        source,
        LintOptions(styles=frozenset({StyleRule.BARE_URL})),
    )
    assert diagnostics == []


def test_valid_reference_footnote_fragment_and_image_are_quiet() -> None:
    source = (
        "# Target Heading\n\n"
        "[reference][ref] and [fragment](#target-heading) and ![diagram](image.png).\n\n"
        "Text[^note].\n\n"
        "[ref]: https://example.com\n"
        "[^note]: Footnote.\n"
    )
    assert lint_text(source) == []


def test_local_file_and_cross_file_fragment_validation(tmp_path: Path) -> None:
    source_path = tmp_path / "source.md"
    target = tmp_path / "target.md"
    target.write_text("# Existing Heading\n")
    source = (
        "[ok](target.md#existing-heading) "
        "[missing](absent.md) "
        "[bad-fragment](target.md#missing-heading)\n"
    )
    rules = rule_ids(source, source_path=source_path)
    assert "link/missing-local-target" in rules
    assert "link/invalid-fragment" in rules


def test_opt_in_style_rules_are_not_default_policy() -> None:
    source = (
        "* one\n+ two\n\n"
        "~~~python\nx=1\n~~~\n\n"
        "```python\ny=2\n```\n\n"
        "https://example.com\n\n"
        "## Heading.\n\n"
        "<span>html</span>\n"
    )
    default_rules = rule_ids(source)
    assert not any(rule.startswith("style/") for rule in default_rules)

    options = LintOptions(
        styles=frozenset(
            {
                StyleRule.UNORDERED_LIST_MARKER,
                StyleRule.FENCE_MARKER,
                StyleRule.BARE_URL,
                StyleRule.HEADING_PUNCTUATION,
                StyleRule.REQUIRE_H1,
                StyleRule.NO_INLINE_HTML,
            }
        )
    )
    styled = rule_ids(source, options=options)
    assert {
        "style/unordered-list-marker",
        "style/fence-marker",
        "style/bare-url",
        "style/heading-punctuation",
        "style/required-h1",
        "style/no-inline-html",
    } <= styled


def test_line_length_is_an_explicit_policy_and_skips_code() -> None:
    source = "ordinary line that is definitely long\n\n```text\nthis code line is also definitely long\n```\n"
    diagnostics = lint_text(source, LintOptions(max_line_length=20))
    line_length = [d for d in diagnostics if d.rule == "style/line-length"]
    assert [d.line for d in line_length] == [1]


def test_reference_labels_are_case_and_whitespace_normalized() -> None:
    source = "[use][Some   Label]\n\n[some label]: /target\n"
    rules = rule_ids(source)
    assert "reference/undefined" not in rules
    assert "reference/unused-definition" not in rules


def test_duplicate_ids_inside_math_or_code_are_not_pandoc_attribute_ids() -> None:
    source = "$\\{#same\\}$\n\n```text\n{#same}\n{#same}\n```\n"
    assert "pandoc/duplicate-identifier" not in rule_ids(source)


# Plain-text cells from a real research document, with no `$` anywhere.
MATH_IN_PROSE = (
    "Passage from bilinear form b: M⊗_R M→R on free M≅R^n to polynomial "
    "b(x,x)∈R[x_0..x_{n-1}]; Lambert series \\sum a_n q^n/(1-q^n) and "
    "Mobius inversion \\mu(n/d), graded \\[a_i\\].\n"
)


def _findings(source: str, rule: str) -> list[str]:
    return [
        source.splitlines()[d.line - 1][d.column - 1 : d.end_column - 1]
        for d in lint_text(source)
        if d.rule == rule and d.line == d.end_line
    ]


def test_tex_notation_outside_math_mode_is_reported() -> None:
    """
    Outside `$...$`, `_` is an emphasis delimiter (here marko and pandoc disagree
    on whether `_R ... x_` is one span), and `\\sum` is raw TeX that pandoc drops
    from HTML output. Each site is named where it stands.
    """
    assert _findings(MATH_IN_PROSE, "math/outside-math-mode") == [
        "_R",
        "^n",
        "_0",
        "_{n-1}",
        "\\sum",
        "_n",
        "^n",
        "^n",
        "\\mu",
    ]


def test_unicode_math_symbols_outside_math_mode_are_reported() -> None:
    assert _findings(MATH_IN_PROSE, "math/unicode-symbol") == ["⊗", "→", "≅", "∈"]


def test_unmatched_backtick_uses_pandoc_table_cell_parsing() -> None:
    """
    Pandoc can parse a code span across pipe table rows. A later, closed code
    span in prose still protects its contents from the math-notation rule.
    """
    source = (
        "| a | b |\n| --- | --- |\n| stray ` | y |\n| `x_0` | `R^n` |\n\n"
        "A stray ` backtick.\n\nThen `x_0 in R^n` in code.\n"
    )
    math_findings = [
        diagnostic for diagnostic in lint_text(source)
        if diagnostic.rule == "math/outside-math-mode"
    ]
    assert math_findings
    assert all(diagnostic.line < 8 for diagnostic in math_findings)


def test_unicode_math_symbols_are_reported_in_code_and_math_too() -> None:
    """Only a fence that names its language keeps Unicode: Lean's syntax uses it."""
    source = (
        "Code `x ∈ M`, math $α$.\n\n"
        "```\nψ_p(∇): T → End(E)\n```\n\n"
        "```lean\ntheorem t : ∀ n : ℕ, n = n := fun _ => rfl\n```\n"
    )
    assert _findings(source, "math/unicode-symbol") == ["∈", "α", "ψ", "∇", "→"]


def test_headings_with_inline_math_or_code_are_still_headings() -> None:
    """
    A heading that contains `$...$` or a code span is a heading: its explicit and
    automatic identifiers resolve fragments, and it counts for duplicates.
    """
    source = (
        "## Plain $x\\to y$ heading {#custom-id}\n\n"
        "## The `run_all` command\n\n"
        "## Sets $A_i \\otimes B$ and $\\pi_1$\n\n"
        "[x](#custom-id), [y](#the-run_all-command), "
        "[z](#sets-a_i-otimes-b-and-pi_1)\n\n"
        "## The `run_all` command\n"
    )
    rules = rule_ids(source)
    assert "link/invalid-fragment" not in rules
    assert "heading/duplicate" in rules


def test_intraword_underscores_stay_in_heading_identifiers() -> None:
    """Pandoc's `intraword_underscores` keeps `is_simple`'s `_` as text, and in the id."""
    source = "## The is_simple check\n\n[a](#the-is_simple-check)\n"
    assert "link/invalid-fragment" not in rule_ids(source)


def test_a_fragment_after_bracketed_link_text_is_not_prose() -> None:
    """Link text holding `$R[[t]]$` still ends in a destination, not prose."""
    source = "## Operators on $R[[t]]$ and $\\partial_t$\n\n"
    source += "- [Operators on $R[[t]]$](#operators-on-rt-and-partial_t)\n"
    assert "math/outside-math-mode" not in rule_ids(source)


def test_emphasis_padding_ignores_bullets_and_adjacent_strong_spans() -> None:
    """
    A `*` bullet is not an emphasis opener, and the words between two bold spans
    are not one padded span. Pandoc reads both lines' emphasis correctly.
    """
    source = (
        "* A *topological group* is a group object in Top.\n\n"
        "The **maximal elliptic** subdiagrams and **maximal parabolic** ones.\n"
    )
    assert "emphasis/padding" not in rule_ids(source)


def test_backslash_math_delimiters_follow_house_source_style() -> None:
    """
    The house Pandoc dialect reads both forms as math. The linter reports the
    delimiter style, while the math content stays outside prose checks.
    """
    source = "Inline \\(x_i\\) here.\n\n\\[\ny^n\n\\]\n"
    assert _findings(source, "math/backslash-delimiter") == ["\\(", "\\)", "\\[", "\\]"]
    assert _findings(source, "math/outside-math-mode") == []


def test_escaped_list_markers_are_not_math_delimiters() -> None:
    """Flowmark writes `1\\)`, `A\\)` and `\\(1)` so a wrapped line is not a list."""
    source = "- Bounds (Thms. 3.4, App.\n  A\\) give enclosures, step\n  \\(1) holds.\n"
    assert "math/backslash-delimiter" not in rule_ids(source)


def test_math_notation_rules_are_quiet_on_prose_code_math_and_urls() -> None:
    source = (
        "Prose with an em dash — and is_simple, __init__, snake_case_name.\n\n"
        "Pandoc sub/superscript: H~2~O and x^2^. Emphasis: _word_ and *word*.\n\n"
        "Math $M \\otimes_R M \\to R$, $x_{n-1}$, and code `x_0 in R^n`.\n\n"
        "See https://example.com/a_b/x_1 and [doc](notes/file_1.md).\n"
    )
    rules = rule_ids(source)
    assert "math/outside-math-mode" not in rules
    assert "math/unicode-symbol" not in rules

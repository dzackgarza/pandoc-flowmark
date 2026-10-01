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
        ("[x]: /a\n[x]: /b\n\n[x][]\n", "reference/duplicate-definition"),
        ("[text]()\n", "link/empty-destination"),
        ("![](image.png)\n", "accessibility/image-alt"),
        ("[click here](https://example.com)\n", "link/non-descriptive-text"),
        ("# Heading\n\n[bad](#missing)\n", "link/invalid-fragment"),
        ("[^unused]: note\n", "footnote/unused-definition"),
        ("Text[^x].\n\n[^x]: one\n[^x]: two\n", "footnote/duplicate-definition"),
        ("---\ntitle: A\ntitle: B\n---\n\nText\n", "frontmatter/duplicate-key"),
        ("---\ntitle: [oops\n---\n\nText\n", "frontmatter/malformed-flow"),
        ("```\ncode\n```\n", "code/missing-language"),
        ("```python\n\tx=1\n```\n", "code/hard-tab"),
        ("# A {#x}\n\n# B {#x}\n", "pandoc/duplicate-identifier"),
        ("::: theorem\nText\n", "pandoc/unclosed-fenced-div"),
        (
            "::: {.theorem}\n## Heading inside div\n:::\n",
            "structure/heading-in-fenced-div",
        ),
        (
            ":::: {.theorem}\n\n::: {.proof}\nText.\n:::\n::::\n",
            "structure/nested-fenced-div",
        ),
        ("Inline $x_i_j$.\n", "math/repeated-subscript"),
        ("Inline $x^2^3$.\n", "math/repeated-superscript"),
        ("Inline $y^{2}^\\alpha$.\n", "math/repeated-superscript"),
        ("Inline $x_{i$.\n", "math/unclosed-group"),
        ("Inline $x_i}$.\n", "math/unmatched-group-close"),
        ("Inline $\\left(x$.\n", "math/unclosed-left"),
        ("Inline $x\\right)$.\n", "math/unmatched-right"),
        ("Inline $Hom_R(M,N)$.\n", "math/bare-operator"),
        ("$$\nSpec R \\to Proj S\n$$\n", "math/bare-operator"),
    ],
)
def test_default_rules_cover_common_structural_and_semantic_failures(
    source: str, expected: str
) -> None:
    assert expected in rule_ids(source)


def test_semantic_diagnostics_do_not_depend_on_formatter_spelling() -> None:
    diagnostics = lint_text("Use _emphasis_.\n\n# Heading\n\n[bad](#missing)\n")
    assert {d.rule for d in diagnostics} == {"link/invalid-fragment"}


@pytest.mark.parametrize(
    "source",
    [
        "[text][missing]\n",
        "[unused]: /target\n",
        "Text[^missing].\n",
        "Text\n```python\nx=1\n```\nAfter\n",
        "Text\n| a | b |\n| --- | --- |\n| x | y |\nAfter\n",
    ],
)
def test_default_linter_does_not_invent_structure_pandoc_did_not_report(
    source: str,
) -> None:
    assert lint_text(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "#Heading\n",
        "(text)[https://example.com]\n",
        "[text](https://example.com]\n",
        "Use * text * here.\n",
        "# A {#x .foo\n",
        "```python\ncode\n",
        "\\begin{align}\nx &= y\n",
        "\\begin{align}\nx &= y\n\\end{equation}\n",
        "\\end{align}\n",
    ],
)
def test_pandoc_prose_is_not_reclassified_as_failed_syntax(source: str) -> None:
    assert lint_text(source) == []


def test_pandoc_allows_atx_heading_levels_above_six() -> None:
    assert lint_text("####### Heading\n") == []


def test_heading_inside_nested_fenced_div_is_structural_rule_at_any_level() -> None:
    source = (
        "# Outside\n\n::: {.definition}\n::: {.proof}\n####### Deep heading\n:::\n:::\n"
    )
    findings = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "structure/heading-in-fenced-div"
    ]
    assert len(findings) == 1
    assert findings[0].line == 5


def test_heading_lookalike_in_code_fence_inside_div_is_not_a_heading_rule() -> None:
    source = "::: {.example}\n~~~markdown\n## Literal heading example\n~~~\n:::\n"
    assert "structure/heading-in-fenced-div" not in rule_ids(source)


def test_nested_fenced_div_warns_on_inner_opener() -> None:
    source = ":::: {.definition}\nOuter.\n\n::: {.proof}\nInner.\n:::\n::::\n"
    findings = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "structure/nested-fenced-div"
    ]
    assert len(findings) == 1
    assert findings[0].line == 4
    assert findings[0].column == 1


def test_each_nested_div_beyond_top_level_warns() -> None:
    source = (
        "::::: {.outer}\n\n:::: {.middle}\n\n::: {.inner}\nText.\n:::\n::::\n:::::\n"
    )
    findings = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "structure/nested-fenced-div"
    ]
    assert [finding.line for finding in findings] == [3, 5]


def bold_label_findings(source: str) -> list[tuple[int, int, str]]:
    return [
        (diagnostic.line, diagnostic.column, diagnostic.message)
        for diagnostic in lint_text(source)
        if diagnostic.rule == "structure/bold-label"
    ]


@pytest.mark.parametrize(
    ("source", "environment"),
    [
        ("**Question.** What is $x$?\n", "question"),
        ("__Proof.__ Trivial.\n", "proof"),
        ("**Remark**: The map is open.\n", "remark"),
        ("**Main Theorem.** Every group acts.\n", "theorem"),
        ("**Definition 1.2 (Weyl).** A group.\n", "definition"),
        ("**Exercise**\n\nShow that $G$ is abelian.\n", "exercise"),
        ("> **Note.** Quoted.\n", "note"),
        ("::: {.example}\n**Warning.** Careful.\n:::\n", "warning"),
    ],
)
def test_bold_run_in_label_warns_with_environment(
    source: str, environment: str
) -> None:
    findings = bold_label_findings(source)
    assert len(findings) == 1
    assert f"`::: {{.{environment}}}`" in findings[0][2]


@pytest.mark.parametrize(
    "source",
    [
        "Not **bold.** here.\n",
        "**Bold** words open this sentence.\n",
        "- **Item.** In a list.\n",
        "~~~markdown\n**Question.** Literal.\n~~~\n",
        "**A bold opening sentence that runs far past any label length limit at all.** "
        "Text.\n",
    ],
)
def test_bold_text_that_is_not_a_run_in_label_does_not_warn(source: str) -> None:
    assert bold_label_findings(source) == []


def test_bold_label_location_skips_earlier_unflagged_bold_paragraphs() -> None:
    source = (
        "**Bold** words open this sentence.\n"
        "**wrapped** continuation line.\n\n"
        "# Section\n"
        "**Question.** What?\n"
    )
    assert [finding[:2] for finding in bold_label_findings(source)] == [(5, 1)]


def test_bold_label_location_follows_lists_and_display_math() -> None:
    source = (
        "- **Item.** In a list.\n"
        "**Lemma.** Lazy continuation.\n\n"
        "$$\nx\n$$\n\n"
        "**$G$-sets.** Defined here.\n\n"
        "  **Theorem.** Indented.\n"
    )
    assert [finding[:2] for finding in bold_label_findings(source)] == [(8, 1), (10, 3)]


def test_bold_label_without_environment_name_lists_choices() -> None:
    (finding,) = bold_label_findings(
        "**Why care** about this?\n\nText.\n\n**Why care**\n"
    )
    assert "`::: {.theorem}`, `::: {.definition}` or `::: {.remark}`" in finding[2]


def test_bold_numbered_section_title_suggests_heading() -> None:
    (finding,) = bold_label_findings("**1.4. Not the whole story.**\n")
    assert finding[2].endswith("Use a Markdown heading.")


def manual_numbering(source: str) -> list[tuple[int, str]]:
    return [
        (diagnostic.line, source.splitlines()[diagnostic.line - 1])
        for diagnostic in lint_text(source)
        if diagnostic.rule == "numbering/manual"
    ]


@pytest.mark.parametrize(
    "source",
    [
        "# 1 Introduction\n",
        "## 2.3. Picard groups\n",
        "## Chapter 4\n",
        "**Theorem 2.6.** Every group acts.\n",
        "**Proposition 1.1 (Weyl).** Text.\n",
        "*Lemma 3* Text.\n",
        "Definition 1.2. A group is a set.\n",
        "Exercise 4: Show it.\n",
        "> **Remark 5.** Quoted.\n",
        '::: {.theorem title="Theorem 3"}\nText.\n:::\n',
        "$$\nx = y \\tag{3.1}\n$$\n",
    ],
)
def test_hand_numbered_items_are_errors(source: str) -> None:
    (diagnostic,) = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "numbering/manual"
    ]
    assert diagnostic.severity == "error"


@pytest.mark.parametrize(
    "source",
    [
        "# Introduction\n",
        "## 2021-05-06 Lecture\n",
        "::: {.theorem #thm:main}\nText.\n:::\n\nBy @thm:main.\n",
        "Theorem 2.6 of the book says so.\n",
        "By [@hartshorne, Theorem 2.6] it holds.\n",
        "$$\nx = y \\tag{*}\n$$\n",
        "~~~markdown\n**Theorem 2.6.** Literal.\n~~~\n",
        "1. First item.\n2. Second item.\n",
    ],
)
def test_unnumbered_items_and_external_references_pass(source: str) -> None:
    assert manual_numbering(source) == []


def test_references_to_hand_numbers_in_the_document_are_errors() -> None:
    source = (
        "## 2 Groups\n\n"
        "**Lemma 2.1.** Text.\n\n"
        "$$\nx \\tag{4}\n$$\n\n"
        "By Lemma 2.1, Equation (4) and § 2, but not Theorem 7.\n"
        "See [@book, Lemma 2.1] and Lemma 2.1 of [Man99].\n"
    )
    assert [line for line, _text in manual_numbering(source)] == [1, 3, 6, 9, 9, 9]


def test_references_match_hand_numbers_only_within_their_family() -> None:
    source = "**Theorem 2.** Text.\n\nSee Chapter 2 and equation (2) of the book.\n"
    assert [line for line, _text in manual_numbering(source)] == [1]


def test_nested_div_opener_without_space_before_attributes_is_located() -> None:
    source = "::::{.outer}\n\n:::{.inner}\nText.\n:::\n::::\n"
    (finding,) = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "structure/nested-fenced-div"
    ]
    assert finding.line == 3


def test_heading_changed_by_smart_typography_is_located() -> None:
    source = "# Notes\n\n## Borel's theor-- \"fixed\" points...\n\n## Borel's theorem\n\n## Borel's theorem\n"
    (finding,) = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "heading/duplicate"
    ]
    assert finding.line == 7


def test_sibling_fenced_divs_do_not_warn_as_nested() -> None:
    source = "::: {.first}\nOne.\n:::\n\n::: {.second}\nTwo.\n:::\n"
    assert "structure/nested-fenced-div" not in rule_ids(source)


def test_div_fence_lookalike_inside_code_does_not_affect_nested_div_reconciliation() -> (
    None
):
    source = (
        ":::: {.outer}\n\n"
        "~~~markdown\n"
        "::: {.not-a-div}\n"
        "~~~\n\n"
        "::: {.inner}\n"
        "Text.\n"
        ":::\n"
        "::::\n"
    )
    findings = [
        diagnostic
        for diagnostic in lint_text(source)
        if diagnostic.rule == "structure/nested-fenced-div"
    ]
    assert len(findings) == 1
    assert findings[0].line == 7


def test_nested_native_html_divs_are_not_fenced_div_diagnostics() -> None:
    source = "<div>\n<div>\nText.\n</div>\n</div>\n"
    assert "structure/nested-fenced-div" not in rule_ids(source)


def test_padded_link_text_is_valid_pandoc_link_not_malformed_syntax() -> None:
    assert "link/text-padding" not in rule_ids("[ text ](https://example.com)\n")


@pytest.mark.parametrize(
    "source",
    [
        "Inline $x_i with no closer.\n",
        "Inline \\(x_i with no closer.\n",
        "\\[\nx_i\n",
    ],
)
def test_unparsed_math_openers_are_not_reclassified_as_math_errors(source: str) -> None:
    # Pandoc leaves these as ordinary text because no Math node is formed. The
    # linter may diagnose TeX *inside recognized Math*, but it must not invent a
    # second delimiter grammar to infer attempted math from prose.
    assert not any(rule.startswith("math/unclosed-") for rule in rule_ids(source))


def test_math_code_and_raw_tex_are_opaque_to_markdown_rules() -> None:
    source = "Math $[x][missing] * text * x_i$, \\(y_j [bad](url]\\), and \\underline{z_k}.\n\n```text\n#Heading\n[text][missing]\nhttps://example.com\n```\n"
    diagnostics = lint_text(
        source,
        LintOptions(styles=frozenset({StyleRule.BARE_URL})),
    )
    assert diagnostics == []


def test_semantic_math_macros_and_braced_scripts_are_quiet() -> None:
    source = (
        r"$x_{i_j}, x_i^j, \Hom_R(M,N), \operatorname{Spec} R, \sin x$"
        "\n"
        r"\[ \mathrm{Hom}(M,N) \to \operatorname{Proj}(S) \]"
        "\n"
    )
    rules = rule_ids(source)
    assert "math/repeated-subscript" not in rules
    assert "math/repeated-superscript" not in rules
    assert "math/bare-operator" not in rules


def test_escaped_braces_and_tex_comments_do_not_corrupt_math_group_balance() -> None:
    source = "$\\left\\{ x_{i} \\right\\}$\n\n$$\nx_{i} % a comment with unmatched } and \\right\n+ y_{j}\n$$\n"
    rules = rule_ids(source)
    assert not any(
        rule
        in {
            "math/unclosed-group",
            "math/unmatched-group-close",
            "math/unclosed-left",
            "math/unmatched-right",
        }
        for rule in rules
    )


def test_tex_and_math_examples_inside_code_fences_are_literal() -> None:
    source = "```tex\n\\begin{align}\n$x_i_j = Hom(M,N)$\n\\end{equation}\n```\n"
    rules = rule_ids(source)
    assert "tex/mismatched-environment" not in rules
    assert "math/repeated-subscript" not in rules
    assert "math/bare-operator" not in rules


def test_valid_reference_footnote_fragment_and_image_are_quiet() -> None:
    source = "# Target Heading\n\n[reference][ref] and [fragment](#target-heading) and ![diagram](image.png).\n\nText[^note].\n\n[ref]: https://example.com\n[^note]: Footnote.\n"
    assert lint_text(source) == []


def test_local_file_and_cross_file_fragment_validation(tmp_path: Path) -> None:
    source_path = tmp_path / "source.md"
    target = tmp_path / "target.md"
    target.write_text("# Existing Heading\n")
    source = "[ok](target.md#existing-heading) [missing](absent.md) [bad-fragment](target.md#missing-heading)\n"
    rules = rule_ids(source, source_path=source_path)
    assert "link/missing-local-target" in rules
    assert "link/invalid-fragment" in rules


def test_opt_in_style_rules_are_not_default_policy() -> None:
    source = "* one\n+ two\n\n~~~python\nx=1\n~~~\n\n```python\ny=2\n```\n\nhttps://example.com\n\n## Heading.\n\n<span>html</span>\n"
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


def test_missing_pandoc_frontmatter_resources_are_path_aware(tmp_path: Path) -> None:
    source_path = tmp_path / "paper.md"
    (tmp_path / "refs.bib").write_text("@book{ok, title={OK}}\n")
    (tmp_path / "second.bib").write_text("@book{second, title={Second}}\n")
    source = (
        "---\n"
        "bibliography: [refs.bib, missing-inline.bib]\n"
        "include-in-header:\n"
        "  - second.bib\n"
        "  - headers/missing.tex\n"
        "csl: styles/missing.csl\n"
        "template: named-template\n"
        "---\n\n"
        "Text.\n"
    )
    diagnostics = lint_text(source, source_path=source_path)
    missing = [d for d in diagnostics if d.rule == "pandoc/missing-resource"]
    assert len(missing) == 3
    assert {d.data["resource"] for d in missing} == {
        "missing-inline.bib",
        "headers/missing.tex",
        "styles/missing.csl",
    }


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
    Outside `$...$`, `_` is an emphasis delimiter, and `\\sum` is raw TeX that pandoc drops
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
    ]  # `\[a_i\]` is math: the dialect enables `tex_math_single_backslash`.


def test_unicode_math_symbols_outside_math_mode_are_reported() -> None:
    assert _findings(MATH_IN_PROSE, "math/unicode-symbol") == ["⊗", "→", "≅", "∈"]


def test_an_unmatched_backtick_does_not_unprotect_later_code_spans() -> None:
    """
    A code span cannot cross a blank line or a table row, so a stray backtick in
    one block must not pair with the first backtick of the next and turn every
    later code span inside out.
    """
    source = (
        "| a | b |\n| --- | --- |\n| stray ` | y |\n| `x_0` | `R^n` |\n\n"
        "A stray ` backtick.\n\nThen `x_0 in R^n` in code.\n"
    )
    assert "math/outside-math-mode" not in rule_ids(source)


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


def rule_lines(source: str, rule: str) -> list[int]:
    return [
        diagnostic.line
        for diagnostic in lint_text(source)
        if diagnostic.rule == rule and diagnostic.severity == "error"
    ]


def test_yaml_keys_after_the_front_matter_are_errors() -> None:
    source = (
        "---\ntitle: Candidates\ntags:\n  - coble\n---\n"
        "notes: |-\n  Provenance: a notebook.\n\n"
        "# Candidates\n\n"
        "status: partial\nunit: computation\n\n"
        "::: {.remark}\naliases:\n  - other\n:::\n"
    )
    assert rule_lines(source, "structure/yaml-in-body") == [6, 11, 15]


def test_unclosed_front_matter_is_yaml_in_body() -> None:
    assert rule_lines("---\ntitle: A\n", "structure/yaml-in-body") == [2]


@pytest.mark.parametrize(
    "source",
    [
        "---\nnotes: |-\n  Text.\n---\n\nBody.\n",
        "Note: this is prose.\n",
        "Here are the keys:\n\n- one\n",
        "```yaml\nnotes: |-\n  Text.\n```\n",
        "- item: value\n",
        "See https://example.com for more.\n",
    ],
)
def test_prose_and_literal_yaml_are_not_yaml_in_body(source: str) -> None:
    assert rule_lines(source, "structure/yaml-in-body") == []


def test_file_paths_in_inline_code_are_errors() -> None:
    source = (
        "Provenance: `/home/user/notebooks/Coble Lattice Invariants.ipynb` and\n"
        "`.../sage-scripts/init.sage`.\n\n"
        "`content_pandoc/sections/Open_Problems/Open_Problems.md` records it.\n\n"
        "See `~/notes/x.md`, `../draft.tex` and `README.md`.\n"
    )
    assert rule_lines(source, "link/file-path") == [1, 2, 4, 6, 6, 6]


@pytest.mark.parametrize(
    "source",
    [
        "Call `np.array` on `x/y`.\n",
        "The `and/or` case and `https://example.com/a.md`.\n",
        "[`notes/a.md`](notes/a.md) is a link.\n",
        "```sh\ncat /home/user/a.md\n```\n",
        "Run `lem:divisibilityTcoOne` and `1/2`.\n",
    ],
)
def test_code_that_is_not_a_file_path_passes(source: str) -> None:
    assert rule_lines(source, "link/file-path") == []

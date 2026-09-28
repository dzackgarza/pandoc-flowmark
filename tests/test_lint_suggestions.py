"""Applying a diagnostic's suggested fix produces the document the author meant."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowmark.lint import LintDiagnostic, LintOptions, StyleRule, lint_text
from flowmark.lint_cli import main


def _offset(text: str, line: int, column: int) -> int:
    lines = text.splitlines(keepends=True)
    return sum(len(item) for item in lines[: line - 1]) + column - 1


def apply_fix(text: str, diagnostic: LintDiagnostic, title: str) -> str:
    """Replace the diagnostic's range with the suggestion named ``title``."""

    suggestion = next(item for item in diagnostic.suggestions if item.title == title)
    start = _offset(text, diagnostic.line, diagnostic.column)
    end = _offset(text, diagnostic.end_line, diagnostic.end_column)
    return text[:start] + suggestion.replacement + text[end:]


def only(diagnostics: list[LintDiagnostic], rule: str) -> LintDiagnostic:
    matching = [item for item in diagnostics if item.rule == rule]
    assert len(matching) == 1, matching
    return matching[0]


def tex_options(macros: Path) -> LintOptions:
    return LintOptions(
        context={"tex": {"macro_sources": [str(macros)], "packages": ["amssymb"]}},
        discover_plugins=False,
    )


@pytest.mark.parametrize(
    ("source", "title", "fixed"),
    [
        ("Let $sin x = 0$.\n", "Use `\\sin`", "Let $\\sin x = 0$.\n"),
        (
            "Let $Hom(A, B)$.\n",
            "Use `\\operatorname{Hom}`",
            "Let $\\operatorname{Hom}(A, B)$.\n",
        ),
    ],
)
def test_bare_operator_fix_uses_the_latex_operator(
    source: str, title: str, fixed: str
) -> None:
    diagnostic = only(lint_text(source), "math/bare-operator")
    assert apply_fix(source, diagnostic, title) == fixed


def test_misspelled_macro_fix_restores_the_defined_macro(tmp_path: Path) -> None:
    macros = tmp_path / "macros.tex"
    macros.write_text("\\newcommand{\\ftd}{F_{2d}}\n")
    source = "The lattice $\\fdt$ is even.\n"
    diagnostic = only(lint_text(source, tex_options(macros)), "tex/unknown-command")
    # The transposed spelling ranks above other one-edit neighbours such as `\ft`.
    assert diagnostic.suggestions[0].replacement == "\\ftd"
    fixed = apply_fix(source, diagnostic, "Use `\\ftd`")
    assert fixed == "The lattice $\\ftd$ is even.\n"
    assert not any(
        item.rule == "tex/unknown-command"
        for item in lint_text(fixed, tex_options(macros))
    )


def test_macro_candidate_fix_writes_the_call_with_the_authored_arguments(
    tmp_path: Path,
) -> None:
    macros = tmp_path / "macros.tex"
    macros.write_text(
        "\\newcommand{\\pair}[2]{\\left\\langle #1, #2 \\right\\rangle}\n"
    )
    source = "Use $\\langle a+b, c\\rangle$.\n"
    diagnostic = only(
        lint_text(source, tex_options(macros)), "math/user-macro-candidates"
    )
    assert (
        apply_fix(source, diagnostic, "Use `\\pair{a+b}{c}`")
        == "Use $\\pair{a+b}{c}$.\n"
    )


def test_notation_fix_uses_the_majority_form() -> None:
    source = "$\\epsilon + \\epsilon + \\varepsilon$\n"
    diagnostic = only(lint_text(source), "math/notation-consistency")
    assert (
        apply_fix(source, diagnostic, "Use `\\epsilon`")
        == "$\\epsilon + \\epsilon + \\epsilon$\n"
    )


def test_undefined_reference_fix_uses_the_same_label_under_its_defined_prefix() -> None:
    source = "See @eq:main.\n"
    start = source.index("@eq:main")
    context: dict[str, object] = {
        "references": {
            "reference_families": ["eq", "fig"],
            "snapshot": {
                "definitions": [],
                "occurrences": [
                    {
                        "key": "eq:main",
                        "family": "eq",
                        "range": {"from": start, "to": start + len("@eq:main")},
                    }
                ],
            },
            "resolutions": {
                "eq:main": {"status": "missing"},
                "fig:main": {"status": "resolved"},
            },
        }
    }
    diagnostic = only(
        lint_text(source, LintOptions(context=context, discover_plugins=False)),
        "reference/missing-workspace-definition",
    )
    assert apply_fix(source, diagnostic, "Use `@fig:main`") == "See @fig:main.\n"


def test_missing_citation_fix_uses_the_nearest_bibliography_key(tmp_path: Path) -> None:
    bibliography = tmp_path / "refs.bib"
    bibliography.write_text(
        "@article{Nikulin1980, author={Nikulin, V. V.}, title={Integral symmetric "
        "bilinear forms}, journal={Math. USSR Izv.}, year={1980}}\n"
        "@book{Conway1999, author={Conway, J. H. and Sloane, N. J. A.}, "
        "title={Sphere Packings}, publisher={Springer}, year={1999}}\n"
    )
    source = "As shown in [@Nikulin1979; @Conway1999].\n"
    context: dict[str, object] = {"references": {"bibliographies": [str(bibliography)]}}
    diagnostic = only(
        lint_text(source, LintOptions(context=context, discover_plugins=False)),
        "citation/missing-bibliography-entry",
    )
    assert (
        apply_fix(source, diagnostic, "Use `@Nikulin1980`")
        == "As shown in [@Nikulin1980; @Conway1999].\n"
    )


def test_cli_reports_suggestions_in_json_and_as_help_lines(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = tmp_path / "note.md"
    document.write_text("Let $sin x = 0$.\n")

    assert main(["--format", "json", "--exit-zero", "--no-config", str(document)]) == 0
    payload = json.loads(capsys.readouterr().out)
    diagnostic = payload["files"][0]["diagnostics"][0]
    assert diagnostic["suggestions"] == [
        {"title": "Use `\\sin`", "replacement": "\\sin"}
    ]

    assert main(["--exit-zero", "--no-config", str(document)]) == 0
    assert "\n  help: Use `\\sin`" in capsys.readouterr().out


def _mixed_citation(source: str) -> LintDiagnostic:
    context: dict[str, object] = {"references": {"reference_families": ["fig"]}}
    return only(
        lint_text(source, LintOptions(context=context, discover_plugins=False)),
        "citation/mixed-reference-types",
    )


def test_cross_reference_and_citation_in_one_group_are_joined_with_a_word() -> None:
    # Adjacent groups would render "fig. 1 (Smith 2020)", which credits the
    # figure to Smith; "fig. 1 and (Smith 2020)" names two separate things.
    source = "Compare [@fig:a; @smith2020].\n"
    diagnostic = _mixed_citation(source)
    assert (
        apply_fix(source, diagnostic, "Use `@fig:a and [@smith2020]`")
        == "Compare @fig:a and [@smith2020].\n"
    )


def test_group_with_a_locator_gets_no_automatic_rewrite() -> None:
    diagnostic = _mixed_citation("Compare [@fig:a; @smith2020, p. 3].\n")
    assert diagnostic.suggestions == ()


def test_bare_url_fix_wraps_only_the_url() -> None:
    source = (
        "Visit https://en.wikipedia.org/wiki/K3_(surface), then (see https://x.org). "
        "A [link](https://y.org) is fine.\n"
    )
    options = LintOptions(styles=frozenset({StyleRule.BARE_URL}))
    fixed = source
    # Apply right-to-left so earlier offsets stay valid.
    for diagnostic in sorted(
        (item for item in lint_text(source, options) if item.rule == "style/bare-url"),
        key=lambda item: (item.line, item.column),
        reverse=True,
    ):
        fixed = apply_fix(fixed, diagnostic, diagnostic.suggestions[0].title)
    assert fixed == (
        "Visit <https://en.wikipedia.org/wiki/K3_(surface)>, then (see <https://x.org>). "
        "A [link](https://y.org) is fine.\n"
    )

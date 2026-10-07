"""Semantic script choices preserve the complete authored operand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowmark.lint import LintDiagnostic, LintOptions, fix_text, lint_text
from flowmark.lint_cli import main


def apply_fix(source: str, diagnostic: LintDiagnostic, title: str) -> str:
    lines = source.splitlines(keepends=True)
    start = sum(map(len, lines[: diagnostic.line - 1])) + diagnostic.column - 1
    end = sum(map(len, lines[: diagnostic.end_line - 1])) + diagnostic.end_column - 1
    suggestion = next(item for item in diagnostic.suggestions if item.title == title)
    return source[:start] + suggestion.replacement + source[end:]


def script_options() -> LintOptions:
    return LintOptions(
        context={
            "tex": {
                "macros": {
                    "units": [r"{#1}^{\times}", 1],
                    "chaincomplex": [r"{#1}^{\scriptscriptstyle\bullet}", 1],
                    "ascendingfiltration": [r"{#1}^{\bullet}", 1],
                    "descendingfiltration": [r"{#1}_{\bullet}", 1],
                    "nonzeroof": [r"{#1}^{\bullet}", 1],
                    "dualof": [r"{#1}^{\vee}", 1],
                    "marked": [r"{#1}^{\diamond}", 1],
                    "reduced": [r"{#1}_{\mathrm{red}}", 1],
                    "separableclosure": [r"{#1}^{\operatorname{sep}}", 1],
                },
            },
        },
        discover_plugins=False,
    )


@pytest.mark.parametrize(
    "mark",
    [
        r"\bullet",
        r"\times",
        "*",
        r"\ast",
        r"\star",
        r"\diamond",
        r"\scriptscriptstyle \bullet",
    ],
)
@pytest.mark.parametrize(
    "operand", ["A", r"A_{\mathfrak p}", r"\mathcal{O}", r"{A/I}", r"\frac{A}{I}"]
)
def test_script_choices_apply_to_whole_operands(mark: str, operand: str) -> None:
    source = f"The object is ${operand}^{{{mark}}}$.\n"
    findings = [
        d
        for d in lint_text(source, script_options())
        if d.rule == "math/script-macro-candidates"
    ]
    assert len(findings) == 1
    diagnostic = findings[0]
    argument = operand[1:-1] if operand.startswith("{") else operand
    assert {s.replacement for s in diagnostic.suggestions} == {
        f"\\{name}{{{argument}}}"
        for name in (
            "units",
            "chaincomplex",
            "ascendingfiltration",
            "nonzeroof",
            "dualof",
            "marked",
        )
    }
    assert (
        apply_fix(source, diagnostic, f"Use `\\units{{{argument}}}`")
        == f"The object is $\\units{{{argument}}}$.\n"
    )
    assert diagnostic.fix is None


def test_repeated_scripts_keep_quotients_and_powers_intact() -> None:
    source = r"$A^{\ast}/(A^{\ast})^{3}$"
    diagnostics = [
        d
        for d in lint_text(source, script_options())
        if d.rule == "math/script-macro-candidates"
    ]
    assert len(diagnostics) == 2
    result = source
    for diagnostic in reversed(diagnostics):
        result = apply_fix(result, diagnostic, r"Use `\units{A}`")
    assert result == r"$\units{A}/(\units{A})^{3}$"
    assert fix_text(source, script_options()).text == source


def test_lower_scripts_offer_lower_indexed_objects() -> None:
    source = r"$G_\ast$"
    (diagnostic,) = [
        d
        for d in lint_text(source, script_options())
        if d.rule == "math/script-macro-candidates"
    ]
    assert [s.replacement for s in diagnostic.suggestions] == [
        r"\descendingfiltration{G}"
    ]
    assert (
        apply_fix(source, diagnostic, r"Use `\descendingfiltration{G}`")
        == r"$\descendingfiltration{G}$"
    )


@pytest.mark.parametrize(
    "source",
    [
        r"$\units{A} + \chaincomplex{C} + A^2 + B^{\bullet+1}$",
        r"`$A^\bullet$`",
        "```tex\nA^\\bullet\n```\n",
        r"$A^{\bulletprime}$",
    ],
)
def test_script_rule_respects_math_and_script_boundaries(source: str) -> None:
    assert not [
        d
        for d in lint_text(source, script_options())
        if d.rule == "math/script-macro-candidates"
    ]


def test_script_rule_uses_only_available_unary_macros() -> None:
    options = LintOptions(
        context={"tex": {"macros": {"units": [r"{#1}^{\times}", 1]}}},
        discover_plugins=False,
    )
    (diagnostic,) = [
        d
        for d in lint_text(r"$A^\bullet$", options)
        if d.rule == "math/script-macro-candidates"
    ]
    assert [s.replacement for s in diagnostic.suggestions] == [r"\units{A}"]
    assert diagnostic.fix is None
    assert not [
        d
        for d in lint_text(r"$A^\bullet$", LintOptions(discover_plugins=False))
        if d.rule == "math/script-macro-candidates"
    ]


def test_cli_serializes_script_choices(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "objects.md"
    document.write_text("Objects:\n\n$A^\\ast$.\n")
    context = tmp_path / "context.json"
    context.write_text(json.dumps(script_options().context))
    assert (
        main(
            [
                "--format",
                "json",
                "--exit-zero",
                "--no-config",
                "--no-discover-plugins",
                "--context",
                str(context),
                str(document),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    (diagnostic,) = [
        d
        for d in payload["files"][0]["diagnostics"]
        if d["rule"] == "math/script-macro-candidates"
    ]
    assert {s["replacement"] for s in diagnostic["suggestions"]} == {
        r"\units{A}",
        r"\chaincomplex{A}",
        r"\ascendingfiltration{A}",
        r"\nonzeroof{A}",
        r"\dualof{A}",
        r"\marked{A}",
    }
    assert diagnostic["line"] == 3
    assert diagnostic["column"] == 2
    assert diagnostic["end_column"] == 8
    assert diagnostic["fix"] is None

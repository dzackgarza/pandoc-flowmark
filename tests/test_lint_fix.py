"""A batch fix applies exactly the machine-applicable fixes and keeps the meaning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowmark.lint import LintOptions, StyleRule, fix_text, lint_text
from flowmark.lint_cli import main


def test_fix_text_applies_every_machine_applicable_fix() -> None:
    source = "- one\n- two\n* three\n\nLet $sin x = cos y$. See https://x.org.\n"
    options = LintOptions(
        styles=frozenset({StyleRule.BARE_URL, StyleRule.UNORDERED_LIST_MARKER}),
        discover_plugins=False,
    )
    result = fix_text(source, options)
    assert result.text == (
        "- one\n- two\n- three\n\nLet $\\sin x = \\cos y$. See <https://x.org>.\n"
    )
    assert sorted(item.rule for item in result.applied) == [
        "math/bare-operator",
        "math/bare-operator",
        "style/bare-url",
        "style/unordered-list-marker",
    ]
    assert not any(item.fix for item in result.remaining)


def test_citation_group_rewrite_is_a_fix() -> None:
    context: dict[str, object] = {"references": {"reference_families": ["fig"]}}
    result = fix_text(
        "Compare [@fig:a; @smith2020].\n",
        LintOptions(context=context, discover_plugins=False),
    )
    assert result.text == "Compare @fig:a and [@smith2020].\n"


def test_a_fix_that_needs_a_choice_is_left_to_the_author(tmp_path: Path) -> None:
    macros = tmp_path / "macros.tex"
    macros.write_text("\\newcommand{\\ftd}{F_{2d}}\n")
    options = LintOptions(
        context={"tex": {"macro_sources": [str(macros)], "packages": ["amssymb"]}},
        discover_plugins=False,
    )
    source = "The lattice $\\fdt$ is even, and $\\epsilon + \\varepsilon$.\n"
    result = fix_text(source, options)
    assert result.text == source
    assert result.applied == ()
    unknown = next(d for d in result.remaining if d.rule == "tex/unknown-command")
    assert unknown.suggestions
    assert unknown.fix is None
    assert all(d.fix is None for d in lint_text(source, options))


def test_cli_fix_rewrites_the_file_and_reports_what_is_left(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = tmp_path / "note.md"
    document.write_text("Let $sin x = 0$ and $\\fdt$.\n")
    assert (
        main(["--fix", "--format", "json", "--exit-zero", "--no-config", str(document)])
        == 0
    )
    assert document.read_text() == "Let $\\sin x = 0$ and $\\fdt$.\n"
    result = json.loads(capsys.readouterr().out)["files"][0]
    assert result["fixes"] == 1
    assert [d["rule"] for d in result["diagnostics"]] == ["tex/unknown-command"]


def test_cli_fix_refuses_stdin(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--fix", "-"])
    assert "--fix rewrites files" in capsys.readouterr().err

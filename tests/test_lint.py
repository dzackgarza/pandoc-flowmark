"""Standalone Flowmark linter contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowmark.lint import LintOptions, lint_rules, lint_text
from flowmark.lint_cli import main


def test_math_and_raw_tex_are_not_markdown_style_findings() -> None:
    source = (
        "Subscripts $x_i y_j$, \\(u_i v_j\\), and \\[a_i b_j\\] stay mathematical; "
        "so does the raw TeX argument in \\underline{x_i}.\n"
    )
    assert lint_text(source) == []


def test_formatter_normalization_is_not_a_lint_diagnostic() -> None:
    assert lint_text("Use _emphasis_ and __strong__ here.\n") == []


def test_linter_does_not_invent_math_from_unparsed_dollar_text() -> None:
    # Pandoc does not produce a Math node for this source. A linter cannot call
    # it "unterminated math" without independently inventing author intent.
    # The `_i` is still TeX notation in prose, and only that is reported.
    rules = [item.rule for item in lint_text("An unmatched $x_i expression.\n")]
    assert rules == ["math/outside-math-mode"]


def test_multiline_pandoc_inline_math_is_not_reported_as_unterminated() -> None:
    # Regression for Pandoc math parsing. Pandoc's mathInlineWith explicitly
    # permits a single physical newline; the old preflight counted dollars per
    # line and emitted two contradictory "unterminated $" errors.
    source = (
        "summand of $B\\cong U\\oplus U\\oplus\\latI_{0,7}$; then "
        "$e^{\\perp B} = \\ZZ e\\oplus\n"
        "U\\oplus\\latI_{0,7}$ and "
        "$e^{\\perp}/e\\cong U\\oplus\\latI_{0,7}\\cong\\latI_{1,8}$,\n"
    )
    macros = {"latI": "\\mathrm{I}", "ZZ": "\\mathbb{Z}"}
    assert lint_text(source, LintOptions(context={"tex": {"macros": macros}})) == []


def test_json_cli_is_editor_consumable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "doc.md"
    path.write_text("[x]: /a\n[x]: /b\n\n[x][]\n")
    assert main(["--format", "json", "--exit-zero", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1
    assert payload["files"][0]["path"] == str(path)
    diagnostic = next(
        item
        for item in payload["files"][0]["diagnostics"]
        if item["rule"] == "reference/duplicate-definition"
    )
    assert diagnostic["rule"] == "reference/duplicate-definition"
    assert diagnostic["line"] == 2
    assert diagnostic["column"] == 1


def test_json_cli_uses_source_path_for_stdin_local_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source_path = tmp_path / "source.md"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("[missing](nope.md)\n"))
    assert (
        main(
            ["--format", "json", "--exit-zero", "--source-path", str(source_path), "-"]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    rules = {item["rule"] for item in payload["files"][0]["diagnostics"]}
    assert "link/missing-local-target" in rules


def test_cli_style_switches_enable_opt_in_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "doc.md"
    path.write_text("https://example.com\n")
    assert (
        main(["--format", "json", "--exit-zero", "--style", "bare-url", str(path)]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    rules = {item["rule"] for item in payload["files"][0]["diagnostics"]}
    assert "style/bare-url" in rules


def test_named_rule_can_be_disabled_or_change_severity() -> None:
    source = "::: {.theorem}\n## Inside\n:::\n"
    assert any(
        diagnostic.rule == "structure/heading-in-fenced-div"
        for diagnostic in lint_text(source)
    )
    assert not any(
        diagnostic.rule == "structure/heading-in-fenced-div"
        for diagnostic in lint_text(
            source,
            LintOptions(rules={"structure/heading-in-fenced-div": "off"}),
        )
    )
    overridden = next(
        diagnostic
        for diagnostic in lint_text(
            source,
            LintOptions(rules={"structure/heading-in-fenced-div": "error"}),
        )
        if diagnostic.rule == "structure/heading-in-fenced-div"
    )
    assert overridden.severity.value == "error"


def test_unknown_rule_id_is_rejected_instead_of_silently_ignored() -> None:
    with pytest.raises(ValueError, match="Unknown lint rule"):
        lint_text("Text.\n", LintOptions(rules={"heading/typo-rule": "off"}))


def test_cli_discovers_rule_config_from_flowmark_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "doc.md"
    path.write_text("::: {.theorem}\n## Inside\n:::\n")
    (tmp_path / "flowmark.toml").write_text(
        '[lint.rules]\n"structure/heading-in-fenced-div" = "off"\n'
    )
    assert main(["--format", "json", "--exit-zero", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    rules = {item["rule"] for item in payload["files"][0]["diagnostics"]}
    assert "structure/heading-in-fenced-div" not in rules


def test_cli_loads_custom_rule_plugin_with_context_and_rule_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin = tmp_path / "custom_rule.py"
    plugin.write_text(
        "from flowmark.lint_engine import LintRule, RuleFinding\n"
        "\n"
        "def register_lint_rules(registry):\n"
        "    def check(context, options):\n"
        "        marker = str(context.data.get('marker', options.get('marker', 'PLUGIN')))\n"
        "        start = context.text.find(marker)\n"
        "        if start < 0:\n"
        "            return []\n"
        "        return [RuleFinding('custom/marker', 'warning', 'custom marker', start, start + len(marker))]\n"
        "    registry.register(LintRule('custom/marker', 'Example custom rule.', check=check))\n"
    )
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"marker": "CUSTOM"}))
    path = tmp_path / "doc.md"
    path.write_text("Before CUSTOM after.\n")

    assert (
        main(
            [
                "--format",
                "json",
                "--exit-zero",
                "--plugin",
                str(plugin),
                "--context",
                str(context),
                "--rule",
                "custom/marker=error",
                str(path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    diagnostic = next(
        item
        for item in payload["files"][0]["diagnostics"]
        if item["rule"] == "custom/marker"
    )
    assert diagnostic["severity"] == "error"
    assert diagnostic["line"] == 1
    assert diagnostic["column"] == 8


def test_duplicate_plugin_declarations_are_idempotent(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "duplicate_safe_plugin.py"
    plugin.write_text(
        "from flowmark.lint_engine import LintRule\n"
        "\n"
        "def register_lint_rules(registry):\n"
        "    registry.register(LintRule('custom/duplicate-safe', 'Duplicate-safe plugin.'))\n"
    )
    rules = lint_rules(
        LintOptions(
            plugins=(str(plugin), str(plugin)),
            discover_plugins=False,
        )
    )
    assert sum(rule.name == "custom/duplicate-safe" for rule in rules) == 1


def test_cli_lists_named_rules_without_a_document(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--list-rules", "--format", "json", "--no-config"]) == 0
    payload = json.loads(capsys.readouterr().out)
    rules = {item["name"]: item for item in payload["rules"]}
    assert rules["structure/heading-in-fenced-div"]["default_level"] == "warning"
    assert rules["style/bare-url"]["default_level"] == "off"

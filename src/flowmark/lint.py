"""Standalone Pandoc-aware Markdown linting with Pandoc as syntax authority.

Every lint pass first invokes the canonical Pandoc reader dialect.  Correctness rules
receive that exact JSON AST and warning stream; source scanners may locate an
already-proven node but may not independently decide that Markdown syntax exists.

The reported rules are explicit semantic/structural checks (references, headings,
links, footnotes, frontmatter, Pandoc attributes, code fences, and TeX checks inside
math which the Pandoc grammar actually recognizes). The formatter's historical
``preflight`` heuristics are deliberately not lint rules: they guess author intent
from source spelling and do not have source-backed Pandoc grammar semantics.

Formatting is deliberately not a lint concern.  Whether source text differs from
Flowmark's canonical rendering is answered by the formatter/check surface, not by
editor diagnostics.  A linter finding therefore always identifies something that
requires author judgment or cannot be repaired uniquely by normalization.

Optional house-style policies are selected with :class:`StyleRule`; they are separate
from the default correctness rules so valid Pandoc Markdown is not rejected merely for
being written in another conventional style.

The public result is editor-neutral.  A CLI, an editor, CI, or a pre-commit hook can all
consume the same diagnostics without importing GUI/editor code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from flowmark.lint_engine import (
    LintRule,
    RuleContext,
    RuleFinding,
    RuleLevel,
    RuleRegistry,
    RuleSetting,
    Suggestion,
    apply_rule_policy,
    load_lint_plugins,
    normalize_rule_settings,
    run_registered_rules,
    validate_rule_settings,
)
from flowmark.lint_authoring import register_authoring_rules
from flowmark.lint_rules import (
    StyleRule,
    register_builtin_rules,
)
from flowmark.pandoc_lint import (
    PandocLintUnavailableError,
    PandocMessage,
    parse_pandoc_for_lint,
)


class Severity(StrEnum):
    """Severity levels emitted by Flowmark's linter."""

    INFO = "info"
    ERROR = "error"
    WARNING = "warning"


def _empty_object_mapping() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class LintDiagnostic:
    """One source diagnostic using 1-based line/column coordinates."""

    rule: str
    severity: Severity
    message: str
    line: int
    column: int
    end_line: int
    end_column: int
    suggestions: tuple[Suggestion, ...] = ()
    data: Mapping[str, object] = field(default_factory=_empty_object_mapping)

    def to_json(self) -> dict[str, object]:
        """Return the stable JSON representation consumed by editor clients."""
        result = asdict(self)
        result["severity"] = self.severity.value
        return result


@dataclass(frozen=True)
class LintOptions:
    """Semantic lint policy.

    Formatting options intentionally do not exist here.  Flowmark's formatter owns
    normalization; the linter owns correctness, ambiguity, and explicitly requested
    authoring policies.
    """

    styles: frozenset[StyleRule] = field(default_factory=frozenset)
    max_line_length: int | None = None
    rules: Mapping[str, object] = field(default_factory=_empty_object_mapping)
    plugins: tuple[str, ...] = ()
    context: Mapping[str, object] = field(default_factory=_empty_object_mapping)
    discover_plugins: bool = True


def _offset_to_point(text: str, offset: int) -> tuple[int, int]:
    """Convert a source offset to a 1-based ``(line, column)`` pair."""
    offset = min(max(offset, 0), len(text))
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset - last_newline
    return line, column


def _point_to_offset(text: str, line: int, column: int) -> int:
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    line_index = max(0, min(len(lines) - 1, line - 1))
    start = sum(len(item) for item in lines[:line_index])
    visible = lines[line_index].rstrip("\r\n")
    return min(start + len(visible), start + max(0, column - 1))


def _diagnostics_from_findings(
    text: str,
    findings: list[RuleFinding],
) -> list[LintDiagnostic]:
    diagnostics: list[LintDiagnostic] = []
    for finding in findings:
        line, column = _offset_to_point(text, finding.start)
        end_line, end_column = _offset_to_point(text, finding.end)
        diagnostics.append(
            LintDiagnostic(
                rule=finding.rule,
                severity=Severity(finding.severity),
                message=finding.message,
                line=line,
                column=column,
                end_line=end_line,
                end_column=end_column,
                suggestions=finding.suggestions,
                data=finding.data,
            )
        )
    return diagnostics


def _pandoc_message_rule(message: PandocMessage) -> str:
    lowered = message.message.casefold()
    if "error parsing yaml metadata" in lowered or "yaml parse exception" in lowered:
        return "frontmatter/malformed-flow"
    if "duplicate key" in lowered:
        return "frontmatter/duplicate-key"
    if "duplicate link reference" in lowered:
        return "reference/duplicate-definition"
    if "duplicate note reference" in lowered:
        return "footnote/duplicate-definition"
    if "note with key" in lowered and "not used" in lowered:
        return "footnote/unused-definition"
    if "div at line" in lowered and "unclosed" in lowered:
        return "pandoc/unclosed-fenced-div"
    return "pandoc/parse-error" if message.severity == "error" else "pandoc/warning"


def _pandoc_message_finding(text: str, message: PandocMessage) -> RuleFinding:
    line = message.line or 1
    column = message.column or 1
    lines = text.splitlines() or [""]
    line_index = max(0, min(len(lines) - 1, line - 1))
    end_column = min(len(lines[line_index]) + 1, column + 1)
    return RuleFinding(
        rule=_pandoc_message_rule(message),
        severity="error" if message.severity == "error" else "warning",
        message=message.message,
        start=_point_to_offset(text, line, column),
        end=_point_to_offset(text, line, end_column),
    )


_PARSER_RULES = (
    LintRule(
        "frontmatter/malformed-flow",
        "Pandoc cannot parse the YAML metadata block.",
        RuleLevel.ERROR,
    ),
    LintRule(
        "frontmatter/duplicate-key",
        "YAML metadata defines a key more than once.",
    ),
    LintRule(
        "reference/duplicate-definition",
        "Markdown reference label is defined more than once.",
    ),
    LintRule(
        "footnote/duplicate-definition",
        "Footnote label is defined more than once.",
    ),
    LintRule(
        "footnote/unused-definition",
        "Footnote definition is unused.",
    ),
    LintRule(
        "pandoc/unclosed-fenced-div",
        "Pandoc fenced div is not explicitly closed.",
    ),
    LintRule("pandoc/parse-error", "Pandoc parser error.", RuleLevel.ERROR),
    LintRule("pandoc/warning", "Pandoc parser warning."),
)


_STYLE_RULE_IDS = {
    StyleRule.UNORDERED_LIST_MARKER: "style/unordered-list-marker",
    StyleRule.FENCE_MARKER: "style/fence-marker",
    StyleRule.BARE_URL: "style/bare-url",
    StyleRule.HEADING_PUNCTUATION: "style/heading-punctuation",
    StyleRule.REQUIRE_H1: "style/required-h1",
    StyleRule.NO_INLINE_HTML: "style/no-inline-html",
}


def _registry(options: LintOptions) -> RuleRegistry:
    registry = RuleRegistry()
    register_builtin_rules(registry)
    register_authoring_rules(registry)
    registry.register_many(_PARSER_RULES)
    load_lint_plugins(
        registry,
        options.plugins,
        discover_entry_points=options.discover_plugins,
    )
    return registry


def _effective_settings(options: LintOptions) -> dict[str, RuleSetting]:
    settings = normalize_rule_settings(options.rules)
    for style in options.styles:
        _ = settings.setdefault(
            _STYLE_RULE_IDS[style],
            RuleSetting(level=RuleLevel.WARNING),
        )
    if options.max_line_length is not None:
        _ = settings.setdefault(
            "style/line-length",
            RuleSetting(
                level=RuleLevel.WARNING,
                options={"max": options.max_line_length},
            ),
        )
    return settings


def lint_rules(options: LintOptions | None = None) -> tuple[LintRule, ...]:
    """Return the effective built-in + extension rule catalogue."""

    return _registry(options or LintOptions()).rules()


def lint_text(
    text: str,
    options: LintOptions | None = None,
    *,
    source_path: Path | None = None,
) -> list[LintDiagnostic]:
    """Lint one Markdown document without modifying or normalizing it."""
    if options is None:
        options = LintOptions()

    try:
        pandoc = parse_pandoc_for_lint(text)
    except PandocLintUnavailableError as error:
        raise RuntimeError(str(error)) from error

    registry = _registry(options)
    settings = _effective_settings(options)
    validate_rule_settings(registry, settings)
    findings = [_pandoc_message_finding(text, message) for message in pandoc.messages]
    if pandoc.document is not None:
        findings.extend(
            run_registered_rules(
                RuleContext(
                    text=text,
                    pandoc_document=pandoc.document,
                    source_path=source_path,
                    data=options.context,
                ),
                registry,
                settings,
            )
        )
    findings = apply_rule_policy(findings, registry, settings)
    diagnostics = _diagnostics_from_findings(text, findings)
    diagnostics.sort(key=lambda item: (item.line, item.column, item.rule))
    return diagnostics


__all__ = (
    "LintDiagnostic",
    "LintOptions",
    "RuleLevel",
    "Severity",
    "StyleRule",
    "lint_rules",
    "lint_text",
)

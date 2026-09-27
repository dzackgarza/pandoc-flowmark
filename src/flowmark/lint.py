"""Standalone Pandoc-aware Markdown linting built on Flowmark's semantic parser.

The linter has one syntax authority: the same parser and normalizer Flowmark uses to
format Pandoc-flavoured Markdown.  It does not run a second Markdown grammar over the
source.  Consequently math, raw TeX, fenced divs, definition lists, tables, footnotes,
and the other constructs Flowmark models are opaque or structured in exactly the same
places during linting as they are during formatting.

Three layers are reported:

* explicit semantic/structural rules (references, headings, links, footnotes,
  frontmatter, Pandoc attributes, code fences, and malformed math/TeX constructs);
* ``pandoc/ambiguous-input`` high-confidence ambiguity checks from
  :func:`flowmark.preflight.preflight`;
* ``format/canonical`` source ranges whose spelling differs from Flowmark's canonical
  rendering under the requested formatting policy.

Optional house-style policies are selected with :class:`StyleRule`; they are separate
from the default correctness rules so valid Pandoc Markdown is not rejected merely for
being written in another conventional style.

The public result is editor-neutral.  A CLI, an editor, CI, or a pre-commit hook can all
consume the same diagnostics without importing CodeMirror or any Zettlr code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path

from flowmark.formats.options import ListSpacing
from flowmark.lint_rules import StyleRule, lint_rule_findings
from flowmark.preflight import preflight
from flowmark.reformat_api import reformat_text


class Severity(StrEnum):
    """Severity levels emitted by Flowmark's linter."""

    ERROR = "error"
    WARNING = "warning"


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
    replacement: str | None = None

    def to_json(self) -> dict[str, object]:
        """Return the stable JSON representation consumed by editor clients."""
        result = asdict(self)
        result["severity"] = self.severity.value
        return result


@dataclass(frozen=True)
class LintOptions:
    """Formatting policy used by the canonical-form rule.

    Defaults deliberately avoid prose reflow and opinionated typography.  They model
    the structural/style checks an editor expects: Flowmark may normalize Markdown
    syntax, but ordinary paragraph line breaking, quotes, ellipses, and list tightness
    are left alone unless the caller explicitly opts in.
    """

    width: int = 0
    semantic: bool = False
    cleanups: bool = False
    smartquotes: bool = False
    ellipses: bool = False
    list_spacing: ListSpacing = ListSpacing.preserve
    check_format: bool = True
    styles: frozenset[StyleRule] = field(default_factory=frozenset)
    max_line_length: int | None = None


def _line_end_column(lines: list[str], line: int) -> int:
    """Return the 1-based column immediately after ``line``'s visible text."""
    if not lines:
        return 1
    index = min(max(line - 1, 0), len(lines) - 1)
    return len(lines[index].rstrip("\n")) + 1


def _format_diagnostics(source: str, normalized: str) -> list[LintDiagnostic]:
    """Convert a line diff against canonical output into source diagnostics."""
    if source == normalized:
        return []

    source_lines = source.splitlines(keepends=True)
    normalized_lines = normalized.splitlines(keepends=True)
    # splitlines() returns [] for an empty document.  The synthetic line keeps
    # insertion diagnostics representable at line 1 without special coordinates.
    coordinate_lines = source_lines or [""]
    matcher = SequenceMatcher(a=source_lines, b=normalized_lines, autojunk=False)
    diagnostics: list[LintDiagnostic] = []

    for (
        tag,
        source_start,
        source_end,
        normalized_start,
        normalized_end,
    ) in matcher.get_opcodes():
        if tag == "equal":
            continue

        first_line = min(source_start + 1, len(coordinate_lines))
        if source_end > source_start:
            last_line = min(source_end, len(coordinate_lines))
        else:
            last_line = first_line
        replacement = "".join(normalized_lines[normalized_start:normalized_end])

        diagnostics.append(
            LintDiagnostic(
                rule="format/canonical",
                severity=Severity.WARNING,
                message="Markdown differs from Flowmark's canonical Pandoc-aware form.",
                line=first_line,
                column=1,
                end_line=last_line,
                end_column=_line_end_column(coordinate_lines, last_line),
                replacement=replacement,
            )
        )

    return diagnostics


def _offset_to_point(text: str, offset: int) -> tuple[int, int]:
    """Convert a source offset to a 1-based ``(line, column)`` pair."""
    offset = min(max(offset, 0), len(text))
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset - last_newline
    return line, column


def _explicit_rule_diagnostics(
    text: str, options: LintOptions, source_path: Path | None
) -> list[LintDiagnostic]:
    diagnostics: list[LintDiagnostic] = []
    for finding in lint_rule_findings(
        text,
        source_path=source_path,
        styles=options.styles,
        max_line_length=options.max_line_length,
    ):
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
                replacement=finding.replacement,
            )
        )
    return diagnostics


def lint_text(
    text: str,
    options: LintOptions | None = None,
    *,
    source_path: Path | None = None,
) -> list[LintDiagnostic]:
    """Lint one Markdown document without modifying it.

    Ambiguous-input findings are returned first.  If they exist, canonical-format
    diagnostics are withheld: formatting malformed or semantically ambiguous input can
    produce secondary spelling differences that are less useful than the root defect.
    """
    if options is None:
        options = LintOptions()

    explicit = _explicit_rule_diagnostics(text, options, source_path)
    ambiguous = [
        LintDiagnostic(
            rule="pandoc/ambiguous-input",
            severity=Severity.ERROR,
            message=finding.message,
            line=finding.line,
            column=1,
            end_line=finding.line,
            end_column=_line_end_column(
                text.splitlines(keepends=True) or [""], finding.line
            ),
        )
        for finding in preflight(text)
    ]
    diagnostics = [*explicit, *ambiguous]
    diagnostics.sort(key=lambda item: (item.line, item.column, item.rule))
    if (
        any(item.severity is Severity.ERROR for item in diagnostics)
        or not options.check_format
    ):
        return diagnostics

    normalized = reformat_text(
        text,
        width=options.width,
        plaintext=False,
        semantic=options.semantic,
        cleanups=options.cleanups,
        smartquotes=options.smartquotes,
        ellipses=options.ellipses,
        list_spacing=options.list_spacing,
        verify=False,
    )
    canonical = [
        item
        for item in _format_diagnostics(text, normalized)
        if not any(
            not (
                item.end_line < explicit_item.line or explicit_item.end_line < item.line
            )
            for explicit_item in diagnostics
        )
    ]
    return [*diagnostics, *canonical]


__all__ = ("LintDiagnostic", "LintOptions", "Severity", "StyleRule", "lint_text")

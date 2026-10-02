"""Excerpts of real notes where flowmark once reported or missed a defect.

Each file in ``tests/notes`` is copied verbatim from a note; only the lines
needed to reproduce the behavior are kept. A test pins every warning and error
the linter reports on it, and checks that formatting keeps its meaning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowmark.lint import LintOptions, fix_text, lint_text
from flowmark.reformat_api import reformat_text

NOTES = Path(__file__).parent / "notes"


def lint_options(tmp_path: Path) -> LintOptions:
    # The one author macro these excerpts use, as the author's macro file
    # defines it.
    macros = tmp_path / "macros.tex"
    macros.write_text("\\newcommand{\\Nod}{{\\mathrm{Nod}}}\n")
    return LintOptions(
        context={"tex": {"macro_sources": [str(macros)]}},
        discover_plugins=False,
    )


def findings(text: str, options: LintOptions) -> list[tuple[int, str]]:
    return [
        (diagnostic.line, diagnostic.rule)
        for diagnostic in lint_text(text, options)
        if diagnostic.severity != "info"
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        # `{#sec-…}` and a later `^{\#}` once read as one Jinja comment, which
        # hid the math between them.
        ("heading-attribute-then-escaped-hash.md", []),
        ("heading-attribute-then-escaped-hash-at-line-start.md", []),
        # `{{1\over 3} …}` in math and a later `}}` once read as one Jinja
        # variable.
        ("double-brace-in-math-then-double-brace.md", []),
        # `\\usepackage` in a YAML block scalar is text to Pandoc, so the
        # package never loads and its commands are undefined.
        (
            "header-includes-escaped-backslash.md",
            [
                (4, "tex/header-includes-text"),
                (9, "tex/unknown-command"),
                (9, "tex/unknown-command"),
                (10, "tex/unknown-command"),
                (10, "tex/unknown-command"),
            ],
        ),
    ),
)
def test_note_excerpt_findings_and_formatting(
    name: str, expected: list[tuple[int, str]], tmp_path: Path
) -> None:
    text = (NOTES / name).read_text()
    assert findings(text, lint_options(tmp_path)) == expected
    reformat_text(text)


def test_escaped_header_includes_excerpt_is_fixed(tmp_path: Path) -> None:
    text = (NOTES / "header-includes-escaped-backslash.md").read_text()
    options = lint_options(tmp_path)
    result = fix_text(text, options)
    assert result.text == text.replace(
        "    \\\\usepackage{dynkin-diagrams}", "    \\usepackage{dynkin-diagrams}"
    )
    assert findings(result.text, options) == []

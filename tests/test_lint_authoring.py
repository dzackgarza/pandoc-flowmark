"""Mathematical-authoring lint rules, driven through lint_text and the CLI."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from flowmark.lint import LintOptions, lint_text
from flowmark.lint_cli import main


def options(context: Mapping[str, object]) -> LintOptions:
    return LintOptions(
        context=context,
        discover_plugins=False,
    )


def tex_context(values: dict[str, object]) -> dict[str, object]:
    return {"tex": values}


def compiler_context(diagnostics: list[dict[str, object]]) -> dict[str, object]:
    return {"compiler": {"tikz": {"diagnostics": diagnostics}}}


def test_tex_macro_sources_drive_unknown_and_candidate_rules(tmp_path: Path) -> None:
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "defs.tex").write_text(
        "\\newcommand{\\RR}{\\mathbb{R}}\n\\newcommand{\\CompilerOnly}[1]{#1}\n"
    )
    source = (
        "TODO: revise.\n\n"
        "Use $\\epsilon_x + \\epsilon_y + \\varepsilon_z$, "
        "$\\RR + \\CompilerOnly{x} + \\mathbb{R}$, and $\\DefinitelyMissing$.\n"
    )
    diagnostics = lint_text(
        source,
        options(
            tex_context(
                {
                    "macro_sources": [str(macros)],
                    "packages": ["amssymb"],
                }
            )
        ),
    )
    rules = {diagnostic.rule for diagnostic in diagnostics}
    assert "tex/unknown-command" in rules
    assert "math/notation-consistency" in rules
    assert "math/user-macro-candidates" in rules
    assert "document/authorial-residue" in rules
    unknown = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.rule == "tex/unknown-command"
    ]
    assert len(unknown) == 1
    assert unknown[0].data == {"command": "\\DefinitelyMissing", "kind": "unknown"}
    prefer = next(
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.rule == "math/user-macro-candidates"
    )
    assert prefer.data == {"candidates": ("\\RR",)}
    assert (
        next(
            d for d in diagnostics if d.rule == "math/notation-consistency"
        ).severity.value
        == "info"
    )


def test_json_macro_source_supplies_names_and_expansions(tmp_path: Path) -> None:
    macros = tmp_path / "macros.json"
    macros.write_text(
        json.dumps(
            {
                "RR": "\\mathbb{R}",
                "pair": ["\\left( #1, #2 \\right)", 2],
            }
        )
    )
    source = "Use $\\RR + \\pair{x}{y} + \\mathbb{R}$.\n"
    diagnostics = lint_text(
        source,
        options(tex_context({"macro_sources": [str(macros)], "packages": ["amssymb"]})),
    )
    assert not any(
        diagnostic.rule == "tex/unknown-command" for diagnostic in diagnostics
    )
    assert any(
        diagnostic.rule == "math/user-macro-candidates"
        and diagnostic.data == {"candidates": ("\\RR",)}
        for diagnostic in diagnostics
    )


def test_manual_notation_lists_every_matching_parameterized_macro(
    tmp_path: Path,
) -> None:
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "angles.tex").write_text(
        "\\newcommand{\\la}{\\langle}\n"
        "\\newcommand{\\ra}{\\rangle}\n"
        "\\newcommand{\\gens}[1]{\\left\\langle{#1}\\right\\rangle}\n"
        "\\newcommand{\\bracket}[1]{\\left\\langle #1 \\right\\rangle}\n"
        "\\newcommand{\\ip}[2]{{\\left\\langle {#1},~{#2} \\right\\rangle}}\n"
    )
    source = "Use $\\langle x,y\\rangle$.\n"
    diagnostics = [
        diagnostic
        for diagnostic in lint_text(
            source,
            options(tex_context({"macro_sources": [str(macros)]})),
        )
        if diagnostic.rule == "math/user-macro-candidates"
    ]
    assert len(diagnostics) == 1
    assert set(cast(list[str], diagnostics[0].data["candidates"])) == {
        "\\bracket{…}",
        "\\gens{…}",
        "\\ip{…}{…}",
    }


def test_parameterized_macro_candidates_respect_required_structure(
    tmp_path: Path,
) -> None:
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "angles.tex").write_text(
        "\\newcommand{\\gens}[1]{\\left\\langle{#1}\\right\\rangle}\n"
        "\\newcommand{\\ip}[2]{{\\left\\langle {#1},~{#2} \\right\\rangle}}\n"
    )
    source = "Use $\\langle x\\rangle$.\n"
    diagnostic = next(
        diagnostic
        for diagnostic in lint_text(
            source,
            options(tex_context({"macro_sources": [str(macros)]})),
        )
        if diagnostic.rule == "math/user-macro-candidates"
    )
    assert diagnostic.data == {"candidates": ("\\gens{…}",)}


def test_texstudio_core_vocabulary_needs_no_external_macro_source() -> None:
    diagnostics = lint_text(
        (
            "Use $\\frac{\\alpha}{R}$, "
            "$f\\colon X\\to Y$, $x\\mapsto f(x)$, "
            "$a\\le b$, $c\\ge d$, $P\\iff Q$, and $x\\gets y$.\n"
        ),
        options({}),
    )
    assert not any(
        diagnostic.rule == "tex/unknown-command" for diagnostic in diagnostics
    )


@pytest.mark.parametrize(
    "source",
    (
        (
            "---\n"
            "header-includes:\n"
            "  - \\usepackage{mathtools}\n"
            "---\n"
            "Use $x\\xmapsto{f}y$, $P\\implies Q$, and "
            "$\\operatorname{Spec}(R)$.\n"
        ),
        (
            "\\usepackage{mathtools}\n\n"
            "Use $x\\xmapsto{f}y$, $P\\implies Q$, and "
            "$\\operatorname{Spec}(R)$.\n"
        ),
    ),
)
def test_texstudio_packages_follow_pandoc_raw_tex_imports(source: str) -> None:
    diagnostics = lint_text(source, options({}))
    assert not any(
        diagnostic.rule == "tex/unknown-command" for diagnostic in diagnostics
    )


def test_texstudio_package_command_reports_inactive_provider() -> None:
    diagnostics = [
        diagnostic
        for diagnostic in lint_text(
            "Use $x\\xmapsto{f}y$.\n",
            options({}),
        )
        if diagnostic.rule == "tex/unknown-command"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].data == {
        "command": "\\xmapsto",
        "kind": "inactive-package",
        "providers": ("mathtools",),
    }


def test_texstudio_package_import_inside_code_is_literal() -> None:
    source = "```tex\n\\usepackage{mathtools}\n```\n\nUse $x\\xmapsto{f}y$.\n"
    diagnostics = [
        diagnostic
        for diagnostic in lint_text(source, options({}))
        if diagnostic.rule == "tex/unknown-command"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].data["kind"] == "inactive-package"


def test_texstudio_packages_can_be_supplied_as_context() -> None:
    diagnostics = lint_text(
        "Use $x\\xmapsto{f}y$.\n",
        options(tex_context({"packages": ["mathtools"]})),
    )
    assert not any(
        diagnostic.rule == "tex/unknown-command" for diagnostic in diagnostics
    )


def test_authorial_residue_ignores_code() -> None:
    source = "TODO outside\n\n~~~text\nTODO ??? [citation needed]\n~~~\n"
    diagnostics = [
        item
        for item in lint_text(source, options({}))
        if item.rule == "document/authorial-residue"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].line == 1


def test_reference_rules_consume_context_data() -> None:
    source = "::: {.theorem .lemma #lem:wrong}\nBody.\n:::\n\nSee @thm:missing.\n"
    context: dict[str, object] = {
        "references": {
            "reference_families": ["thm", "lem", "fig"],
            "theorem_families": ["thm", "lem"],
            "family_aliases": {},
            "referenceable_div_classes": ["theorem", "lemma"],
            "proof_div_classes": ["proof", "sketch", "solution"],
            "theorem_class_to_prefix": {"theorem": "thm", "lemma": "lem"},
            "snapshot": {
                "definitions": [
                    {
                        "key": "lem:wrong",
                        "family": "lem",
                        "sourceKind": "theorem-div",
                        "classes": ["theorem", "lemma"],
                        "range": {"from": 24, "to": 34},
                    }
                ],
                "occurrences": [
                    {
                        "key": "thm:missing",
                        "family": "thm",
                        "range": {
                            "from": source.index("@thm:missing"),
                            "to": source.index("@thm:missing") + 12,
                        },
                    }
                ],
            },
            "resolutions": {
                "lem:wrong": {"status": "resolved"},
                "thm:missing": {"status": "missing"},
            },
        }
    }
    rules = {diagnostic.rule for diagnostic in lint_text(source, options(context))}
    assert "reference/multiple-theorem-classes" in rules
    assert "reference/class-family-mismatch" in rules
    assert "reference/missing-workspace-definition" in rules


def test_reference_div_semantics_are_derived_from_pandoc_ast() -> None:
    source = (
        "::: {.proof .lemma #lem:proof-conflict}\n"
        "Proof and lemma conflict.\n"
        ":::\n\n"
        "::: {.note #thm:untyped}\n"
        "Untyped theorem id.\n"
        ":::\n"
    )
    context: dict[str, object] = {
        "references": {
            "reference_families": ["thm", "lem"],
            "theorem_families": ["thm", "lem"],
            "family_aliases": {},
            "referenceable_div_classes": ["theorem", "lemma"],
            "proof_div_classes": ["proof", "sketch", "solution"],
            "theorem_class_to_prefix": {"theorem": "thm", "lemma": "lem"},
            "snapshot": {"definitions": [], "occurrences": []},
            "resolutions": {},
        }
    }
    diagnostics = lint_text(source, options(context))
    rules = {item.rule for item in diagnostics}
    assert "reference/theorem-proof-class-conflict" in rules
    assert "reference/proof-id-no-target" in rules
    assert "reference/missing-theorem-class" in rules


def test_duplicate_workspace_definition_lists_all_sites() -> None:
    source = "::: {.theorem #thm:dup}\nBody.\n:::\n"
    start = source.index("#thm:dup")
    definition = {
        "key": "thm:dup",
        "family": "thm",
        "sourceKind": "theorem-div",
        "classes": ["theorem"],
        "range": {"from": start, "to": start + len("#thm:dup")},
    }
    context: dict[str, object] = {
        "references": {
            "reference_families": ["thm"],
            "theorem_families": ["thm"],
            "family_aliases": {},
            "referenceable_div_classes": ["theorem"],
            "proof_div_classes": ["proof"],
            "theorem_class_to_prefix": {"theorem": "thm"},
            "snapshot": {"definitions": [definition], "occurrences": []},
            "resolutions": {
                "thm:dup": {
                    "status": "duplicate",
                    "definitions": [
                        {**definition, "documentPath": "/a.md"},
                        {**definition, "documentPath": "/b.md"},
                    ],
                }
            },
        }
    }
    diagnostic = next(
        item
        for item in lint_text(source, options(context))
        if item.rule == "reference/duplicate-workspace-definition"
    )
    assert set(cast(list[str], diagnostic.data["definition_paths"])) == {
        "/a.md",
        "/b.md",
    }


def test_missing_reference_reports_real_cross_family_match() -> None:
    source = "See @eq:main.\n"
    start = source.index("@eq:main")
    context: dict[str, object] = {
        "references": {
            "reference_families": ["eq", "fig"],
            "theorem_families": [],
            "family_aliases": {},
            "referenceable_div_classes": [],
            "proof_div_classes": [],
            "theorem_class_to_prefix": {},
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
                "fig:main": {
                    "status": "resolved",
                    "definition": {"key": "fig:main"},
                },
            },
        }
    }
    diagnostic = next(
        item
        for item in lint_text(source, options(context))
        if item.rule == "reference/missing-workspace-definition"
    )
    assert diagnostic.data["key"] == "eq:main"
    assert diagnostic.data["same_family_candidates"] == ()
    assert diagnostic.data["cross_family_candidates"] == ("fig:main",)


KNOWN_BIB = (
    "@article{FS86, author={Friedman, Robert and Scattone, Francesco}, "
    "title={Type {III} degenerations of {K3} surfaces}, journal={Invent. Math.}, "
    "year={1986}}\n"
)


def test_missing_citations_are_checked_against_the_host_bibliography_files(
    tmp_path: Path,
) -> None:
    bibliography = tmp_path / "references.bib"
    bibliography.write_text(KNOWN_BIB)
    source = "See [@FS86; @Missing; @thm:main].\n"
    context: dict[str, object] = {
        "references": {
            "reference_families": ["thm"],
            "theorem_families": ["thm"],
            "family_aliases": {},
            "bibliographies": [str(bibliography)],
        }
    }
    diagnostics = lint_text(source, options(context))
    rules = {item.rule for item in diagnostics}
    assert "citation/mixed-reference-types" in rules
    missing = [
        item.data
        for item in diagnostics
        if item.rule == "citation/missing-bibliography-entry"
    ]
    assert missing == [{"key": "Missing"}]


def test_document_bibliography_metadata_is_read_relative_to_the_document(
    tmp_path: Path,
) -> None:
    (tmp_path / "bib").mkdir()
    (tmp_path / "bib" / "references.bib").write_text(KNOWN_BIB)
    document = tmp_path / "chapter.md"
    source = (
        "---\nbibliography: bib/references.bib\n---\n\nFollowing @FS86 and @FS87.\n"
    )
    missing = [
        item.data
        for item in lint_text(source, options({}), source_path=document)
        if item.rule == "citation/missing-bibliography-entry"
    ]
    assert missing == [{"key": "FS87"}]


def test_editing_the_bibliography_file_changes_the_known_keys(tmp_path: Path) -> None:
    bibliography = tmp_path / "references.bib"
    bibliography.write_text(KNOWN_BIB)
    context: dict[str, object] = {"references": {"bibliographies": [str(bibliography)]}}
    source = "Following @Nikulin80.\n"

    def missing() -> list[str]:
        return [
            item.rule
            for item in lint_text(source, options(context))
            if item.rule == "citation/missing-bibliography-entry"
        ]

    assert missing() == ["citation/missing-bibliography-entry"]
    bibliography.write_text(
        KNOWN_BIB
        + "@article{Nikulin80, author={Nikulin, V. V.}, title={Integral forms}, "
        + "journal={Izv.}, year={1980}}\n"
    )
    assert missing() == []


def test_tikz_compile_diagnostic_is_extension_context() -> None:
    diagnostics = lint_text(
        "Text.\n",
        options(
            compiler_context(
                [{"from": 0, "to": 4, "message": "TikZ compilation failed: bad macro"}]
            )
        ),
    )
    finding = next(item for item in diagnostics if item.rule == "tikz/compile-error")
    assert finding.severity.value == "error"
    assert finding.line == 1


def test_cli_config_loads_relative_macro_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    macros = tmp_path / "macros"
    macros.mkdir()
    (macros / "project.tex").write_text("\\newcommand{\\ProjectMacro}{\\mathbb{P}}\n")
    document = tmp_path / "paper.md"
    document.write_text("Use $\\ProjectMacro + \\mathbb{P} + \\DefinitelyMissing$.\n")
    config = tmp_path / "flowmark.toml"
    config.write_text(
        '[lint.context.tex]\nmacro_sources = ["macros"]\npackages = ["amssymb"]\n'
    )

    assert main(["--format", "json", "--exit-zero", str(document)]) == 0
    payload = json.loads(capsys.readouterr().out)
    diagnostics = payload["files"][0]["diagnostics"]
    unknown = [item for item in diagnostics if item["rule"] == "tex/unknown-command"]
    assert len(unknown) == 1
    assert unknown[0]["data"] == {"command": "\\DefinitelyMissing", "kind": "unknown"}
    assert any(
        item["rule"] == "math/user-macro-candidates"
        and item["data"] == {"candidates": ["\\ProjectMacro"]}
        for item in diagnostics
    )

"""Math regions in the source are exactly the TeX that Pandoc reads as math."""

from __future__ import annotations

from flowmark.lint_rules import pandoc_math_regions
from flowmark.pandoc_lint import pandoc_math_sequence, parse_pandoc_for_lint


def _region_text_and_pandoc_math(source: str) -> tuple[list[str], list[str]]:
    document = parse_pandoc_for_lint(source).document
    assert document is not None
    # Pandoc trims the spaces inside inline math (`trimMath`); the region keeps them.
    regions = [source[start:end].strip() for start, end in pandoc_math_regions(source)]
    equations = [
        equation.strip() for _display, equation in pandoc_math_sequence(document)
    ]
    return regions, equations


def _generated_cases() -> list[str]:
    cases = [
        "$x$",
        "$$x$$",
        r"\(x\)",
        r"\[x\]",
        "before $x$ after",
        "before $$x$$ after",
        "$ x$",
        "$x $",
        "$x$2",
        "$5",
        "$5 and $6",
        r"$x\text{ $ literal }y$",
        r"$x\text{ {nested $ literal} }y$",
        "$x\n y$",
        "$x\n\n y$",
        r"\(x" + "\n y" + r"\)",
        r"\[x" + "\n y" + r"\]",
        r"\[x" + "\n\n y" + r"\]",
        "$$\nx+y\n$$",
        r"\[" + "\nx+y\n" + r"\]",
        r"escaped \$x$ remains prose",
        r"escaped \\(x\) remains prose",
        r"$\alpha_{i} + \beta^2$",
        r"$\text{cost $5 and \{braces\}} + x$",
        r"\( x + y \)",
        r"\(x\)2",
        "$$$$",
        "$$x$$2",
        "$a$$b$",
        "$a$ $b$",
        "$a$\n$b$",
        "prefix $a\n b$ suffix",
        "prefix $$a\n b$$ suffix",
        "prefix $$a\n\n b$$ suffix",
        "price $420K and then $x$",
        "price $5 and another $10.",
        "a \\$ literal and $x$",
        "$x\\$y$",
        r"\(\text{literal \) text} + x\)",
    ]

    atoms = [
        "$x$",
        "$x+y$",
        r"$x_{i_j}$",
        r"$\text{a $ b}$",
        "$$x+y$$",
        r"\(x+y\)",
        r"\[x+y\]",
        "$x\n+y$",
        "$ x$",
        "$x $",
        "$x$3",
        r"\$x$",
    ]
    prefixes = ["", "before ", "(", "> quote ", "- item ", "alpha; "]
    suffixes = ["", " after", ".", ")", "; omega"]
    for prefix in prefixes:
        for atom in atoms:
            for suffix in suffixes:
                cases.append(f"{prefix}{atom}{suffix}")

    # Pairwise adjacency is where delimiter ownership defects tend to hide.
    for left_index, left in enumerate(atoms):
        for right_index, right in enumerate(atoms):
            if (left_index * 17 + right_index * 31) % 3 == 0:
                cases.append(f"before {left}{right} after")
                cases.append(f"before {left} {right} after")
    return cases


def test_math_regions_hold_pandoc_math_over_a_torture_corpus() -> None:
    cases = _generated_cases()
    assert len(cases) >= 400
    regions, equations = _region_text_and_pandoc_math("\n\n".join(cases))
    assert regions == equations


def test_multiline_inline_math_region_holds_pandoc_math() -> None:
    source = (
        "summand of $B\\cong U\\oplus U\\oplus\\latI_{0,7}$; then "
        "$e^{\\perp B} = \\ZZ e\\oplus\n"
        "U\\oplus\\latI_{0,7}$ and "
        "$e^{\\perp}/e\\cong U\\oplus\\latI_{0,7}\\cong\\latI_{1,8}$,"
    )
    regions, equations = _region_text_and_pandoc_math(source)
    assert regions == equations

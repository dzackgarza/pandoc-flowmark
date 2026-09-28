"""Differential torture oracle for Flowmark's source-positioned Pandoc math scanner."""

from __future__ import annotations

import json
import subprocess
from typing import cast

from flowmark.pandoc_dialect import PANDOC_FORMAT
from flowmark.pandoc_math import iter_pandoc_math_spans
from flowmark.pandoc_reader import pandoc_executable

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


SENTINEL = "FLOWMARK-PANDOC-MATH-CASE-"


def _collect_math(value: JsonValue) -> list[tuple[bool, str]]:
    result: list[tuple[bool, str]] = []
    if isinstance(value, list):
        for item in value:
            result.extend(_collect_math(item))
        return result
    if not isinstance(value, dict):
        return result
    if value.get("t") == "Math":
        content = value.get("c")
        assert isinstance(content, list) and len(content) == 2
        mode = content[0]
        assert isinstance(mode, dict)
        equation = content[1]
        assert isinstance(equation, str)
        result.append((mode.get("t") == "DisplayMath", equation))
        return result
    for child in value.values():
        result.extend(_collect_math(child))
    return result


def _sentinel_index(block: JsonValue) -> int | None:
    if not isinstance(block, dict) or block.get("t") != "RawBlock":
        return None
    content = block.get("c")
    if not isinstance(content, list) or len(content) != 2 or content[0] != "html":
        return None
    raw = content[1]
    if not isinstance(raw, str):
        return None
    prefix = f"<!-- {SENTINEL}"
    if not raw.startswith(prefix) or not raw.endswith(" -->"):
        return None
    return int(raw[len(prefix) : -len(" -->")])


def _pandoc_math_by_case(cases: list[str]) -> list[list[tuple[bool, str]]]:
    batch = "\n\n".join(f"{source}\n\n<!-- {SENTINEL}{index} -->" for index, source in enumerate(cases))
    completed = subprocess.run(
        [pandoc_executable(), "-f", PANDOC_FORMAT, "-t", "json"],
        input=batch,
        text=True,
        capture_output=True,
        check=True,
    )
    document = cast("dict[str, JsonValue]", json.loads(completed.stdout))
    blocks = document.get("blocks")
    assert isinstance(blocks, list)
    result: list[list[tuple[bool, str]]] = [[] for _ in cases]
    current = 0
    for block in blocks:
        sentinel = _sentinel_index(block)
        if sentinel is not None:
            assert sentinel == current
            current += 1
            continue
        assert current < len(cases), "Pandoc emitted content after final sentinel"
        result[current].extend(_collect_math(block))
    assert current == len(cases)
    return result


def _scanner_math(source: str) -> list[tuple[bool, str]]:
    return [(span.display, span.equation) for span in iter_pandoc_math_spans(source)]


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


def test_pandoc_math_scanner_matches_real_pandoc_torture_corpus() -> None:
    cases = _generated_cases()
    assert len(cases) >= 400
    expected = _pandoc_math_by_case(cases)
    for index, (source, pandoc_math) in enumerate(zip(cases, expected, strict=True)):
        assert _scanner_math(source) == pandoc_math, f"case {index}: {source!r}"


def test_workspace_multiline_regression_matches_pandoc_exactly() -> None:
    source = (
        "summand of $B\\cong U\\oplus U\\oplus\\latI_{0,7}$; then "
        "$e^{\\perp B} = \\ZZ e\\oplus\n"
        "U\\oplus\\latI_{0,7}$ and "
        "$e^{\\perp}/e\\cong U\\oplus\\latI_{0,7}\\cong\\latI_{1,8}$,"
    )
    assert _scanner_math(source) == _pandoc_math_by_case([source])[0]

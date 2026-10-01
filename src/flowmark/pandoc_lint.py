"""One exact Pandoc parse pass shared by Flowmark lint rules.

Pandoc owns syntax.  Lint rules may inspect this semantic tree and may use source
scanners only to *locate* a construct whose existence this tree has already
established.  A scanner result which cannot be reconciled to a Pandoc node is not
syntax and must not produce a correctness diagnostic.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Literal, cast

from flowmark.pandoc_dialect import PANDOC_LINT_FORMAT
from flowmark.pandoc_reader import PandocUnavailableError, pandoc_executable

type PandocJson = str | int | float | bool | None | list[PandocJson] | dict[str, PandocJson]


type PandocSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class PandocMessage:
    """One warning/error emitted by the real Pandoc reader."""

    severity: PandocSeverity
    message: str
    line: int | None
    column: int | None


@dataclass(frozen=True)
class PandocLintDocument:
    """Exact Pandoc JSON document plus reader diagnostics."""

    document: dict[str, PandocJson] | None
    messages: tuple[PandocMessage, ...]

    @property
    def parsed(self) -> bool:
        return self.document is not None


class PandocLintUnavailableError(RuntimeError):
    """Raised when syntax-aware linting cannot invoke Pandoc."""


_POINT_RE = re.compile(r"\(line (?P<line>\d+), column (?P<column>\d+)\)|line (?P<line2>\d+) column (?P<column2>\d+)")


def _point(message: str) -> tuple[int | None, int | None]:
    match = _POINT_RE.search(message)
    if match is None:
        return None, None
    line = match.group("line") or match.group("line2")
    column = match.group("column") or match.group("column2")
    return int(line), int(column)


def _messages(stderr: str, unmarked: PandocSeverity) -> tuple[PandocMessage, ...]:
    """Split Pandoc's stderr into messages; a line without a tag has `unmarked`."""
    if not stderr.strip():
        return ()
    lines = stderr.rstrip().splitlines()
    result: list[PandocMessage] = []
    current: list[str] = []
    severity: PandocSeverity = unmarked

    def flush() -> None:
        nonlocal current, severity
        if not current:
            return
        message = "\n".join(current).strip()
        line, column = _point(message)
        result.append(PandocMessage(severity, message, line, column))
        current = []

    for line in lines:
        if line.startswith("[WARNING]"):
            flush()
            severity = "warning"
            current = [line.removeprefix("[WARNING]").strip()]
        elif current:
            current.append(line)
        else:
            severity = unmarked
            current = [line]
    flush()
    return tuple(result)


def parse_pandoc_for_lint(text: str) -> PandocLintDocument:
    """Parse exact authored text with the canonical Pandoc reader dialect."""
    try:
        pandoc = pandoc_executable()
    except PandocUnavailableError as error:
        raise PandocLintUnavailableError(str(error)) from error
    completed = subprocess.run(
        [pandoc, "-f", PANDOC_LINT_FORMAT, "-t", "json"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    messages = _messages(completed.stderr, "error" if completed.returncode != 0 else "warning")
    if completed.returncode != 0:
        return PandocLintDocument(None, messages)
    parsed = cast("dict[str, PandocJson]", json.loads(completed.stdout))
    return PandocLintDocument(parsed, messages)


def walk_pandoc(value: PandocJson) -> list[dict[str, PandocJson]]:
    """Return all tagged Pandoc nodes in document order."""
    result: list[dict[str, PandocJson]] = []

    def visit(node: PandocJson) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("t"), str):
            result.append(node)
        for child in node.values():
            visit(child)

    visit(value)
    return result


def pandoc_plain(value: PandocJson) -> str:
    """Plain visible text of an inline/block subtree for source reconciliation."""
    if isinstance(value, list):
        return "".join(pandoc_plain(item) for item in value)
    if not isinstance(value, dict):
        return ""
    kind = value.get("t")
    content = value.get("c")
    if kind == "Str" and isinstance(content, str):
        return content
    if kind in {"Space", "SoftBreak", "LineBreak"}:
        return " "
    if kind in {"Code", "Math", "RawInline"} and isinstance(content, list) and len(content) >= 2:
        return str(content[1])
    if kind in {"Link", "Image", "Span", "Cite"} and isinstance(content, list) and len(content) >= 2:
        return pandoc_plain(content[1])
    return pandoc_plain(content)


def pandoc_math_sequence(document: dict[str, PandocJson]) -> list[tuple[bool, str]]:
    """Math nodes in exact Pandoc document order."""
    result: list[tuple[bool, str]] = []
    for node in walk_pandoc(document):
        if node.get("t") != "Math":
            continue
        content = node.get("c")
        if not isinstance(content, list) or len(content) != 2:
            continue
        mode, equation = content
        display = isinstance(mode, dict) and mode.get("t") == "DisplayMath"
        if isinstance(equation, str):
            result.append((display, equation))
    return result


__all__ = (
    "PandocJson",
    "PandocLintDocument",
    "PandocLintUnavailableError",
    "PandocMessage",
    "pandoc_math_sequence",
    "pandoc_plain",
    "parse_pandoc_for_lint",
    "walk_pandoc",
)

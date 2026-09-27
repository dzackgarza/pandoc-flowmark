"""Semantic and structural lint rules for Pandoc-flavoured Markdown.

The rule engine is deliberately separate from the formatter-diff rule in
:mod:`flowmark.lint`.  Formatting differences are reported by ``format/canonical``;
this module owns defects that canonical rendering cannot reliably express: broken
references, heading structure, malformed Pandoc constructs, accessibility checks,
frontmatter integrity, and opt-in style policies.

Rules operate on the same source language Flowmark formats.  Inline code/math/raw TeX
and block code/math/TeX regions are protected before syntax-oriented scans run, so a
Markdown rule never interprets TeX punctuation as Markdown punctuation.  Source ranges
are always offsets into the original string and are converted to line/column positions
by the caller.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote, urlsplit

from flowmark.atomic_spans import (
    INLINE_CODE_SPAN,
    PAIRED_HTML_COMMENT,
    PAIRED_JINJA_COMMENT,
    PAIRED_JINJA_TAG,
    PAIRED_JINJA_VAR,
    SINGLE_HTML_COMMENT,
    SINGLE_JINJA_COMMENT,
    SINGLE_JINJA_TAG,
    SINGLE_JINJA_VAR,
    iter_atomic_spans,
)
from flowmark.formats.flowmark_parser import CustomRawInlineTex
from flowmark.linewrapping.atomic_patterns import DOLLAR_MATH, AtomicPattern
from flowmark.markdown_ast import stringify_inlines
from flowmark.pandoc_source import located_nodes, read_source_ast
from flowmark.pandoc_verify import pandoc_executable

# Math as pandoc's `markdown` reads it: `$...$` and `$$...$$` only. It leaves
# `tex_math_single_backslash` off, so `\(...\)` and `\[...\]` are prose.
_DOLLAR_MATH_SPAN = AtomicPattern(name="dollar_math", pattern=DOLLAR_MATH)


class StyleRule(StrEnum):
    """Opt-in policies that are valid Markdown but may violate house style."""

    UNORDERED_LIST_MARKER = "unordered-list-marker"
    FENCE_MARKER = "fence-marker"
    BARE_URL = "bare-url"
    HEADING_PUNCTUATION = "heading-punctuation"
    REQUIRE_H1 = "require-h1"
    NO_INLINE_HTML = "no-inline-html"


@dataclass(frozen=True)
class RuleFinding:
    """One lint finding at a half-open source range."""

    rule: str
    severity: str
    message: str
    start: int
    end: int
    replacement: str | None = None


@dataclass(frozen=True)
class _Line:
    number: int
    start: int
    end: int
    raw_end: int
    text: str


@dataclass(frozen=True)
class _Fence:
    opening: _Line
    closing: _Line | None
    marker: str
    info: str
    content: tuple[_Line, ...]


@dataclass(frozen=True)
class _Heading:
    line: _Line
    level: int
    text: str
    start: int
    end: int
    explicit_id: str | None


@dataclass(frozen=True)
class _Definition:
    label: str
    normalized: str
    line: _Line
    start: int
    end: int
    destination: str


@dataclass(frozen=True)
class _FootnoteDefinition:
    label: str
    normalized: str
    line: _Line
    start: int
    end: int


@dataclass(frozen=True)
class _Frontmatter:
    opening: _Line
    closing: _Line | None
    body: tuple[_Line, ...]


_HEADING_NO_SPACE = re.compile(r"^ {0,3}#{1,6}[^#\s]")
_HEADING_TOO_DEEP = re.compile(r"^ {0,3}#{7,}(?:[ \t]+|$)")
_ATTR_BLOCK = re.compile(r"\{(?P<body>[^{}\n]*)\}")
_REF_DEFINITION = re.compile(
    r"^ {0,3}\[(?P<label>[^\]^\n]+)\]:[ \t]*(?P<dest><[^>\n]*>|\S+)(?:[ \t]+.*)?$"
)
_FOOTNOTE_DEFINITION = re.compile(r"^ {0,3}\[\^(?P<label>[^\]\n]+)\]:")
_FULL_REFERENCE = re.compile(
    r"(?P<image>!?)\[(?P<text>[^\]\n]*)\]\[(?P<label>[^\]\n]*)\]"
)
_BRACKET_TOKEN = re.compile(r"(?P<image>!?)\[(?P<text>[^\]\n]+)\]")
_INLINE_LINK = re.compile(
    r"(?P<image>!?)\[(?P<text>[^\]\n]*)\]\("
    r"(?P<dest><[^>\n]*>|(?:\\.|[^)\n])*)"
    r"(?P<title>[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\)"
)
_EMPTY_LINK = re.compile(r"(?P<image>!?)\[(?P<text>[^\]\n]*)\]\([ \t]*\)")
_REVERSED_LINK = re.compile(r"(?<![!\w])\((?P<text>[^()\n]+)\)\[(?P<dest>[^\]\n]+)\]")
_MALFORMED_LINK_CLOSER = re.compile(r"(?P<image>!?)\[[^\]\n]*\]\([^\n)]*\]")
_SPACED_LINK_TEXT = re.compile(r"(?P<image>!?)\[[ \t]+[^\]\n]*[^\]\s][ \t]+\]\(")
# Emphasis the author padded inside its markers (`* text *`). The opener must stand
# where an opener can (after whitespace, `(`, `[`, or line start) and the closer
# where a closer can (before whitespace, punctuation, or line end); otherwise the
# "span" is the gap between two real spans, as in `**a** and **b**`.
_SPACED_EMPHASIS = re.compile(
    r"(?<![^\s(\[])(?P<marker>\*\*|__|\*|_)[ \t]+(?P<body>[^\n]+?)[ \t]+(?P=marker)"
    r"(?=[\s.,;:!?)\]]|$)",
    re.MULTILINE,
)
_FENCE_OPEN = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_FENCED_DIV_OPEN = re.compile(r"^ {0,3}(?P<fence>:{3,})(?P<attrs>[ \t]+.*)?$")
_LATEX_BEGIN = re.compile(r"\\begin\{(?P<name>[A-Za-z*]+)\}")
_LATEX_END_TEMPLATE = r"\\end\{%s\}"
_EXPLICIT_ID = re.compile(r"\{[^{}\n]*#(?P<id>[A-Za-z][A-Za-z0-9_.:-]*)[^{}\n]*\}")
# One block for inline scanning: a table row or heading line alone, or a run of
# other non-blank lines.
_INLINE_BLOCK = re.compile(
    r"^[ \t]*[|#][^\n]*"
    r"|^(?:[ \t]*[^\s|#][^\n]*(?:\n(?![ \t]*(?:\n|$|[|#]))|$))+",
    re.MULTILINE,
)
_FRAGMENT_DESTINATION = re.compile(r"\]\((?P<dest>#[^()\s]+)\)")
_ANY_URL = re.compile(r"https?://[^\s<>)\]]+")
# TeX written in prose: a control word (`\sum`), or a sub/superscript on a base
# character (`x_0`, `x_{n-1}`, `R^n`, `H^*`). A script is one braced group or one
# character that ends the token, so `is_simple` and pandoc's `x^2^` are left alone.
_TEX_IN_PROSE = re.compile(
    r"\\[A-Za-z]+"
    r"|(?<=[^\s_^~`*\\])[_^](?:\{[^{}\n]*\}|[A-Za-z0-9*](?![A-Za-z0-9_^~]))"
)
_BACKSLASH_DELIMITER_PAIRS = (
    re.compile(r"(?<!\\)\\\((?P<body>(?:(?!\\\)|\n[ \t]*\n).)*?)\\\)", re.DOTALL),
    re.compile(r"(?<!\\)\\\[(?P<body>(?:(?!\\\]|\n[ \t]*\n).)*?)\\\]", re.DOTALL),
)
_INLINE_HTML = re.compile(r"</?[A-Za-z][^>\n]*>")
_UNORDERED_MARKER = re.compile(r"^(?P<indent> *)(?P<marker>[*+-])[ \t]+")
_TOP_LEVEL_YAML_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)[ \t]*:")

_PROTECTED_INLINE_PATTERNS = (
    INLINE_CODE_SPAN,
    _DOLLAR_MATH_SPAN,
    SINGLE_HTML_COMMENT,
    PAIRED_HTML_COMMENT,
    SINGLE_JINJA_TAG,
    PAIRED_JINJA_TAG,
    SINGLE_JINJA_COMMENT,
    PAIRED_JINJA_COMMENT,
    SINGLE_JINJA_VAR,
    PAIRED_JINJA_VAR,
)

_NON_DESCRIPTIVE_LINK_TEXT = {
    "click here",
    "here",
    "link",
    "this link",
    "more",
    "read more",
    "learn more",
}

_HEADING_PUNCTUATION = frozenset(".,;:!?")


def _lines(text: str) -> list[_Line]:
    raw_lines = text.splitlines(keepends=True)
    if not raw_lines:
        return [_Line(1, 0, 0, 0, "")]
    result: list[_Line] = []
    offset = 0
    for number, raw in enumerate(raw_lines, 1):
        visible = raw.rstrip("\r\n")
        result.append(
            _Line(
                number=number,
                start=offset,
                end=offset + len(visible),
                raw_end=offset + len(raw),
                text=visible,
            )
        )
        offset += len(raw)
    return result


def _frontmatter(lines: list[_Line]) -> _Frontmatter | None:
    start = 0
    while start < len(lines) and not lines[start].text.strip():
        start += 1
    if start >= len(lines) or lines[start].text.strip() != "---":
        return None
    body: list[_Line] = []
    for index in range(start + 1, len(lines)):
        if lines[index].text.strip() in {"---", "..."}:
            return _Frontmatter(lines[start], lines[index], tuple(body))
        body.append(lines[index])
    return _Frontmatter(lines[start], None, tuple(body))


def _fences(lines: list[_Line], protected_line_numbers: set[int]) -> list[_Fence]:
    result: list[_Fence] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.number in protected_line_numbers:
            index += 1
            continue
        match = _FENCE_OPEN.match(line.text)
        if match is None:
            index += 1
            continue
        marker = match.group("marker")
        # A backtick info string may not itself contain a backtick.
        if marker[0] == "`" and "`" in match.group("info"):
            index += 1
            continue
        content: list[_Line] = []
        closing: _Line | None = None
        probe = index + 1
        while probe < len(lines):
            candidate = lines[probe]
            stripped = candidate.text.lstrip(" ")
            leading = len(candidate.text) - len(stripped)
            if leading <= 3:
                close_match = re.match(
                    rf"{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$", stripped
                )
                if close_match is not None:
                    closing = candidate
                    break
            content.append(candidate)
            probe += 1
        result.append(
            _Fence(line, closing, marker, match.group("info").strip(), tuple(content))
        )
        index = (probe + 1) if closing is not None else len(lines)
    return result


def _mark(protected: bytearray, start: int, end: int) -> None:
    if end <= start:
        return
    protected[start:end] = b"\x01" * (end - start)


def _build_protected_map(
    text: str,
    lines: list[_Line],
    frontmatter: _Frontmatter | None,
    fences: list[_Fence],
) -> bytearray:
    protected = bytearray(len(text))
    if frontmatter is not None:
        last = frontmatter.closing or (
            frontmatter.body[-1] if frontmatter.body else frontmatter.opening
        )
        _mark(protected, frontmatter.opening.start, last.raw_end)

    for fence in fences:
        last = fence.closing or (fence.content[-1] if fence.content else fence.opening)
        _mark(protected, fence.opening.start, last.raw_end)

    # Block display math and LaTeX environments are verbatim from the Markdown
    # linter's point of view.  Protect complete well-formed blocks; malformed
    # openers are intentionally left visible to the dedicated unclosed rules.
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.text.strip()
        if stripped == "$$":
            probe = index + 1
            while probe < len(lines):
                if lines[probe].text.rstrip().endswith("$$"):
                    _mark(protected, line.start, lines[probe].raw_end)
                    index = probe + 1
                    break
                probe += 1
            else:
                index += 1
            continue
        begin = _LATEX_BEGIN.search(line.text)
        if begin is not None:
            close_re = re.compile(_LATEX_END_TEMPLATE % re.escape(begin.group("name")))
            probe = index
            while probe < len(lines):
                if close_re.search(lines[probe].text) is not None:
                    _mark(protected, line.start + begin.start(), lines[probe].raw_end)
                    index = probe + 1
                    break
                probe += 1
            else:
                index += 1
            continue
        index += 1

    # The inline patterns match across newlines, as a code span may within one
    # paragraph. Scanning the whole document at once let a stray backtick pair with
    # one in a later block and invert every code span after it, so each block --
    # a run of lines between blank lines, with every table row and heading its
    # own block -- is scanned separately.
    for block in _INLINE_BLOCK.finditer(text):
        for span in iter_atomic_spans(block.group(0), _PROTECTED_INLINE_PATTERNS):
            if span.is_atomic:
                _mark(protected, block.start() + span.start, block.start() + span.end)

    raw_tex_pattern = CustomRawInlineTex.pattern
    if isinstance(raw_tex_pattern, re.Pattern):
        for match in raw_tex_pattern.finditer(text):
            _mark(protected, match.start(), match.end())
    return protected


def _overlaps(protected: bytearray, start: int, end: int) -> bool:
    if end <= start:
        return False
    return any(protected[start:end])


def _normalize_reference_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _headings(
    text: str, lines: list[_Line], frontmatter: _Frontmatter | None
) -> list[_Heading]:
    result: list[_Heading] = []
    if frontmatter is not None and frontmatter.closing is None:
        return result
    body_start = (
        frontmatter.closing.raw_end
        if frontmatter is not None and frontmatter.closing is not None
        else 0
    )
    line_offset = frontmatter.closing.number if frontmatter and frontmatter.closing else 0
    for located in located_nodes(read_source_ast(text[body_start:], pandoc_executable())):
        if located.node.get("t") != "Header":
            continue
        content = located.node.get("c")
        if not isinstance(content, list) or len(content) != 3:
            continue
        level, attrs, inlines = content
        if not isinstance(level, int) or not isinstance(attrs, list) or not attrs:
            continue
        original_line = located.source_range.start.line + line_offset
        if not (1 <= original_line <= len(lines)):
            continue
        last_line = min(located.source_range.end.line + line_offset - 1, len(lines))
        while last_line > original_line and not lines[last_line - 1].text.strip():
            last_line -= 1
        identifier = attrs[0] if isinstance(attrs[0], str) else None
        line = lines[original_line - 1]
        result.append(
            _Heading(
                line=line,
                level=level,
                text=stringify_inlines(inlines),
                start=line.start + located.source_range.start.column - 1,
                end=lines[last_line - 1].end,
                explicit_id=identifier,
            )
        )
    return result


_VERBATIM_INLINE = re.compile(rf"`+(?P<code>[^`]+)`+|(?P<math>{DOLLAR_MATH})")


def _plain_inline_text(text: str) -> str:
    """
    The text pandoc's `stringify` gives an inline run: markup removed, while code
    and math keep their text verbatim (`$\\pi_1$` is `\\pi_1`, underscore included).
    """
    parts: list[str] = []
    position = 0
    for match in _VERBATIM_INLINE.finditer(text):
        parts.append(_strip_inline_markup(text[position : match.start()]))
        math = match.group("math")
        parts.append(match.group("code") if math is None else math.strip("$"))
        position = match.end()
    parts.append(_strip_inline_markup(text[position:]))
    return " ".join("".join(parts).split())


def _strip_inline_markup(text: str) -> str:
    text = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", text)
    text = re.sub(r"!?(?:\[([^\]]*)\])\[[^\]]*\]", r"\1", text)
    text = re.sub(r"[*~]", "", text)
    # Pandoc's `intraword_underscores`: an `_` between alphanumerics is text.
    text = re.sub(r"(?<![^\W_])_|_(?![^\W_])", "", text)
    return re.sub(r"<[^>]+>", "", text)


def _heading_identifiers(headings: list[_Heading]) -> set[str]:
    return {
        heading.explicit_id
        for heading in headings
        if heading.explicit_id is not None
    }


def _definitions(lines: list[_Line], protected: bytearray) -> list[_Definition]:
    result: list[_Definition] = []
    for line in lines:
        if _overlaps(protected, line.start, line.end):
            continue
        match = _REF_DEFINITION.match(line.text)
        if match is None:
            continue
        label = match.group("label")
        destination = match.group("dest").strip("<>")
        result.append(
            _Definition(
                label=label,
                normalized=_normalize_reference_label(label),
                line=line,
                start=line.start + match.start("label"),
                end=line.start + match.end("label"),
                destination=destination,
            )
        )
    return result


def _footnote_definitions(
    lines: list[_Line], protected: bytearray
) -> list[_FootnoteDefinition]:
    result: list[_FootnoteDefinition] = []
    for line in lines:
        if _overlaps(protected, line.start, line.end):
            continue
        match = _FOOTNOTE_DEFINITION.match(line.text)
        if match is None:
            continue
        label = match.group("label")
        result.append(
            _FootnoteDefinition(
                label=label,
                normalized=_normalize_reference_label(label),
                line=line,
                start=line.start + match.start("label"),
                end=line.start + match.end("label"),
            )
        )
    return result


def _reference_findings(
    text: str, lines: list[_Line], protected: bytearray
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    definitions = _definitions(lines, protected)
    by_label: dict[str, list[_Definition]] = {}
    for definition in definitions:
        by_label.setdefault(definition.normalized, []).append(definition)

    for group in by_label.values():
        if len(group) > 1:
            for duplicate in group[1:]:
                findings.append(
                    RuleFinding(
                        "reference/duplicate-definition",
                        "warning",
                        f"Reference label {duplicate.label!r} is defined more than once.",
                        duplicate.start,
                        duplicate.end,
                    )
                )

    used: set[str] = set()
    definition_line_numbers = {definition.line.number for definition in definitions}
    for match in _FULL_REFERENCE.finditer(text):
        if _overlaps(protected, match.start(), match.end()):
            continue
        # A definition line can contain bracket pairs in its title/destination;
        # no reference use originates on the definition marker itself.
        line_number = _line_number_for_offset(lines, match.start())
        if line_number in definition_line_numbers:
            continue
        label = match.group("label") or match.group("text")
        normalized = _normalize_reference_label(label)
        if normalized in by_label:
            used.add(normalized)
        else:
            findings.append(
                RuleFinding(
                    "reference/undefined",
                    "warning",
                    f"Reference label {label!r} has no definition.",
                    match.start("label")
                    if match.group("label")
                    else match.start("text"),
                    match.end("label") if match.group("label") else match.end("text"),
                )
            )

    # Shortcut references only have reference semantics when a matching
    # definition exists.  Count those for unused-definition analysis, but never
    # call an arbitrary [word] undefined.
    for match in _BRACKET_TOKEN.finditer(text):
        if _overlaps(protected, match.start(), match.end()):
            continue
        if _line_number_for_offset(lines, match.start()) in definition_line_numbers:
            continue
        if match.end() < len(text) and text[match.end()] in "([":
            continue
        if match.start() > 0 and text[match.start() - 1] == "^":
            continue
        normalized = _normalize_reference_label(match.group("text"))
        if normalized in by_label:
            used.add(normalized)

    for normalized, group in by_label.items():
        if normalized in used:
            continue
        definition = group[0]
        findings.append(
            RuleFinding(
                "reference/unused-definition",
                "warning",
                f"Reference label {definition.label!r} is defined but never used.",
                definition.start,
                definition.end,
            )
        )
    return findings


def _line_number_for_offset(lines: list[_Line], offset: int) -> int:
    # Linear scan is acceptable for rule matches; files are capped at 1 MiB by
    # Flowmark's discovery layer and the number of syntax matches is small.
    for line in lines:
        if line.start <= offset <= line.raw_end:
            return line.number
    return lines[-1].number


def _footnote_findings(
    text: str, lines: list[_Line], protected: bytearray
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    definitions = _footnote_definitions(lines, protected)
    by_label: dict[str, list[_FootnoteDefinition]] = {}
    for definition in definitions:
        by_label.setdefault(definition.normalized, []).append(definition)
    for group in by_label.values():
        if len(group) > 1:
            for duplicate in group[1:]:
                findings.append(
                    RuleFinding(
                        "footnote/duplicate-definition",
                        "warning",
                        f"Footnote {duplicate.label!r} is defined more than once.",
                        duplicate.start,
                        duplicate.end,
                    )
                )

    definition_ranges = {
        (definition.line.start, definition.line.end) for definition in definitions
    }
    used: set[str] = set()
    for match in re.finditer(r"\[\^(?P<label>[^\]\n]+)\]", text):
        if _overlaps(protected, match.start(), match.end()):
            continue
        if any(
            start <= match.start() <= end and text[match.end() : match.end() + 1] == ":"
            for start, end in definition_ranges
        ):
            continue
        label = match.group("label")
        normalized = _normalize_reference_label(label)
        if normalized in by_label:
            used.add(normalized)
        else:
            findings.append(
                RuleFinding(
                    "footnote/undefined",
                    "warning",
                    f"Footnote reference {label!r} has no definition.",
                    match.start("label"),
                    match.end("label"),
                )
            )
    for normalized, group in by_label.items():
        if normalized in used:
            continue
        definition = group[0]
        findings.append(
            RuleFinding(
                "footnote/unused-definition",
                "warning",
                f"Footnote {definition.label!r} is defined but never referenced.",
                definition.start,
                definition.end,
            )
        )
    return findings


def _heading_findings(
    headings: list[_Heading],
    lines: list[_Line],
    protected: bytearray,
    styles: frozenset[StyleRule],
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    previous_level: int | None = None
    seen_text: dict[str, _Heading] = {}
    h1s: list[_Heading] = []
    for heading in headings:
        if previous_level is not None and heading.level > previous_level + 1:
            findings.append(
                RuleFinding(
                    "heading/increment",
                    "warning",
                    f"Heading level jumps from H{previous_level} to H{heading.level}.",
                    heading.start,
                    heading.end,
                )
            )
        previous_level = heading.level
        plain = _plain_inline_text(heading.text).casefold()
        if plain:
            if plain in seen_text:
                findings.append(
                    RuleFinding(
                        "heading/duplicate",
                        "warning",
                        f"Heading {heading.text!r} duplicates an earlier heading.",
                        heading.start,
                        heading.end,
                    )
                )
            else:
                seen_text[plain] = heading
        if heading.level == 1:
            h1s.append(heading)
        if StyleRule.HEADING_PUNCTUATION in styles:
            plain_text = _plain_inline_text(heading.text).rstrip()
            if plain_text and plain_text[-1] in _HEADING_PUNCTUATION:
                findings.append(
                    RuleFinding(
                        "style/heading-punctuation",
                        "warning",
                        "Heading ends in punctuation.",
                        heading.start,
                        heading.end,
                    )
                )
    for heading in h1s[1:]:
        findings.append(
            RuleFinding(
                "heading/multiple-h1",
                "warning",
                "Document contains more than one level-1 heading.",
                heading.start,
                heading.end,
            )
        )
    if StyleRule.REQUIRE_H1 in styles and not h1s:
        first = next((line for line in lines if line.text.strip()), lines[0])
        findings.append(
            RuleFinding(
                "style/required-h1",
                "warning",
                "Document contains no level-1 heading.",
                first.start,
                first.end,
            )
        )

    for line in lines:
        if _overlaps(protected, line.start, line.end):
            continue
        if _HEADING_NO_SPACE.match(line.text):
            findings.append(
                RuleFinding(
                    "heading/malformed",
                    "warning",
                    "ATX heading marker must be followed by whitespace.",
                    line.start,
                    line.end,
                )
            )
        elif _HEADING_TOO_DEEP.match(line.text):
            findings.append(
                RuleFinding(
                    "heading/malformed",
                    "warning",
                    "Markdown headings have at most six levels; this line is parsed as prose.",
                    line.start,
                    line.end,
                )
            )
    return findings


def _fence_language(info: str) -> str | None:
    info = info.strip()
    if not info:
        return None
    if not info.startswith("{"):
        return info.split()[0]
    match = re.search(r"(?:^|\s)\.([A-Za-z0-9_+.-]+)", info.strip("{}"))
    return match.group(1) if match is not None else None


def _fence_findings(
    fences: list[_Fence], lines: list[_Line], styles: frozenset[StyleRule]
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    seen_marker: str | None = None
    for fence in fences:
        if fence.closing is None:
            findings.append(
                RuleFinding(
                    "code/unclosed-fence",
                    "error",
                    "Fenced code block opens here but is never closed.",
                    fence.opening.start,
                    fence.opening.end,
                )
            )
        if _fence_language(fence.info) is None:
            findings.append(
                RuleFinding(
                    "code/missing-language",
                    "warning",
                    "Fenced code block has no language/info class.",
                    fence.opening.start,
                    fence.opening.end,
                )
            )
        for line in fence.content:
            if "\t" in line.text:
                column = line.text.index("\t")
                findings.append(
                    RuleFinding(
                        "code/hard-tab",
                        "warning",
                        "Fenced code block contains a hard tab.",
                        line.start + column,
                        line.start + column + 1,
                    )
                )
        opening_index = fence.opening.number - 1
        if opening_index > 0 and lines[opening_index - 1].text.strip():
            findings.append(
                RuleFinding(
                    "code/surrounding-blank-lines",
                    "warning",
                    "Fenced code block should be preceded by a blank line.",
                    fence.opening.start,
                    fence.opening.end,
                )
            )
        if fence.closing is not None:
            closing_index = fence.closing.number - 1
            if closing_index + 1 < len(lines) and lines[closing_index + 1].text.strip():
                findings.append(
                    RuleFinding(
                        "code/surrounding-blank-lines",
                        "warning",
                        "Fenced code block should be followed by a blank line.",
                        fence.closing.start,
                        fence.closing.end,
                    )
                )
        if StyleRule.FENCE_MARKER in styles:
            marker = fence.marker[0]
            if seen_marker is None:
                seen_marker = marker
            elif marker != seen_marker:
                findings.append(
                    RuleFinding(
                        "style/fence-marker",
                        "warning",
                        "Code fence marker is inconsistent with the first fence in the document.",
                        fence.opening.start,
                        fence.opening.end,
                    )
                )
    return findings


def _frontmatter_findings(frontmatter: _Frontmatter | None) -> list[RuleFinding]:
    if frontmatter is None:
        return []
    if frontmatter.closing is None:
        return [
            RuleFinding(
                "frontmatter/unclosed",
                "error",
                "YAML frontmatter opens here but has no closing '---' or '...'.",
                frontmatter.opening.start,
                frontmatter.opening.end,
            )
        ]

    findings: list[RuleFinding] = []
    seen_keys: dict[str, _Line] = {}
    stack: list[tuple[str, _Line, int]] = []
    quote: str | None = None
    quote_line: _Line | None = None
    quote_column = 0
    for line in frontmatter.body:
        key_match = _TOP_LEVEL_YAML_KEY.match(line.text)
        if key_match is not None:
            key = key_match.group("key")
            if key in seen_keys:
                findings.append(
                    RuleFinding(
                        "frontmatter/duplicate-key",
                        "error",
                        f"Frontmatter key {key!r} is defined more than once.",
                        line.start + key_match.start("key"),
                        line.start + key_match.end("key"),
                    )
                )
            else:
                seen_keys[key] = line

        escaped = False
        index = 0
        while index < len(line.text):
            char = line.text[index]
            if quote is None and char == "#":
                break
            if quote == '"':
                if char == "\\" and not escaped:
                    escaped = True
                    index += 1
                    continue
                if char == '"' and not escaped:
                    quote = None
                escaped = False
                index += 1
                continue
            if quote == "'":
                if char == "'":
                    if index + 1 < len(line.text) and line.text[index + 1] == "'":
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if char in {"'", '"'}:
                quote = char
                quote_line = line
                quote_column = index
            elif char in "[{":
                stack.append((char, line, index))
            elif char in "]}":
                expected = "[" if char == "]" else "{"
                if not stack or stack[-1][0] != expected:
                    findings.append(
                        RuleFinding(
                            "frontmatter/malformed-flow",
                            "error",
                            f"Unexpected {char!r} in YAML frontmatter.",
                            line.start + index,
                            line.start + index + 1,
                        )
                    )
                else:
                    stack.pop()
            index += 1

    if quote is not None and quote_line is not None:
        findings.append(
            RuleFinding(
                "frontmatter/malformed-flow",
                "error",
                "Unterminated quoted scalar in YAML frontmatter.",
                quote_line.start + quote_column,
                quote_line.end,
            )
        )
    for opener, line, column in stack:
        findings.append(
            RuleFinding(
                "frontmatter/malformed-flow",
                "error",
                f"Unclosed {opener!r} flow collection in YAML frontmatter.",
                line.start + column,
                line.start + column + 1,
            )
        )
    return findings


def _explicit_identifier_findings(text: str, protected: bytearray) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    seen: dict[str, tuple[int, int]] = {}
    for match in _EXPLICIT_ID.finditer(text):
        if _overlaps(protected, match.start(), match.end()):
            continue
        identifier = match.group("id")
        if identifier in seen:
            findings.append(
                RuleFinding(
                    "pandoc/duplicate-identifier",
                    "error",
                    f"Pandoc identifier {identifier!r} is used more than once.",
                    match.start("id"),
                    match.end("id"),
                )
            )
        else:
            seen[identifier] = (match.start("id"), match.end("id"))
    return findings


def _attribute_findings(lines: list[_Line], protected: bytearray) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for line in lines:
        if _overlaps(protected, line.start, line.end):
            continue
        # Attribute-looking suffixes are authored after headings/images/div
        # openers.  A missing closing brace is almost certainly a malformed
        # Pandoc attribute block, but ordinary prose braces are not diagnosed.
        match = re.search(r"(?:^|[ \t])\{(?=[#.A-Za-z][^{}\n]*$)", line.text)
        if match is None or "}" in line.text[match.start() :]:
            continue
        prefix = line.text[: match.start()].lstrip()
        if not (
            prefix.startswith("#")
            or prefix.startswith("!")
            or prefix.startswith(":::")
            or prefix.endswith(")")
            or prefix.endswith("]")
        ):
            continue
        start = (
            line.start + match.start() + (1 if match.group(0).startswith(" ") else 0)
        )
        findings.append(
            RuleFinding(
                "pandoc/malformed-attributes",
                "error",
                "Pandoc attribute block opens here but has no closing '}'.",
                start,
                line.end,
            )
        )
    return findings


def _unclosed_construct_findings(
    lines: list[_Line], protected: bytearray
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    # Pandoc fenced divs nest, so track fence lengths as a stack and ignore
    # lines protected by literal code/math blocks.
    div_stack: list[tuple[int, _Line]] = []
    for line in lines:
        if _overlaps(protected, line.start, line.end):
            continue
        match = _FENCED_DIV_OPEN.match(line.text)
        if match is None:
            continue
        fence_len = len(match.group("fence"))
        attrs = (match.group("attrs") or "").strip()
        if not attrs and div_stack:
            div_stack.pop()
        else:
            div_stack.append((fence_len, line))
    for _length, line in div_stack:
        findings.append(
            RuleFinding(
                "pandoc/unclosed-fenced-div",
                "error",
                "Pandoc fenced div opens here but is never closed.",
                line.start,
                line.end,
            )
        )

    # Raw TeX environments: a begin with no later matching end is an error.
    source = "\n".join(line.text for line in lines)
    for match in _LATEX_BEGIN.finditer(source):
        close_re = re.compile(_LATEX_END_TEMPLATE % re.escape(match.group("name")))
        if close_re.search(source, match.end()) is None:
            # Re-map through the line table using source offsets from the
            # newline-normalized join; this is exact for ordinary LF input and
            # conservative for CRLF (the line itself is still correct).
            line_no = source.count("\n", 0, match.start()) + 1
            line = lines[min(line_no - 1, len(lines) - 1)]
            column = match.start() - (source.rfind("\n", 0, match.start()) + 1)
            findings.append(
                RuleFinding(
                    "tex/unclosed-environment",
                    "error",
                    f"LaTeX environment {match.group('name')!r} is opened but never closed.",
                    line.start + column,
                    line.end,
                )
            )
    return findings


def _malformed_inline_findings(text: str, protected: bytearray) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for regex, rule, message in (
        (
            _REVERSED_LINK,
            "link/reversed-syntax",
            "Link syntax appears reversed; use '[text](destination)'.",
        ),
        (
            _MALFORMED_LINK_CLOSER,
            "link/malformed-syntax",
            "Link/image destination closes with ']' instead of ')'.",
        ),
        (
            _SPACED_LINK_TEXT,
            "link/text-padding",
            "Link text has padding spaces inside its brackets.",
        ),
        (
            _SPACED_EMPHASIS,
            "emphasis/padding",
            "Whitespace inside emphasis markers prevents Markdown emphasis parsing.",
        ),
    ):
        for match in regex.finditer(text):
            if _overlaps(protected, match.start(), match.end()):
                continue
            line_start = text.rfind("\n", 0, match.start()) + 1
            if (
                regex is _SPACED_EMPHASIS
                and match.group("marker") == "*"
                and not text[line_start : match.start()].strip()
            ):
                continue  # a `*` bullet, not an emphasis opener
            findings.append(
                RuleFinding(rule, "warning", message, match.start(), match.end())
            )

    # Pandoc's `markdown` reads `\(` as a literal parenthesis and `\[` as a literal
    # bracket. A lone escape is ordinary: `\[1\]` is a literal bracket, and the
    # wrapper writes `1\)`, `A\)` and `\(1)` so a line does not start a list. So a
    # delimiter counts only as a pair, within one block, around TeX-looking text.
    delimiters: list[int] = []
    for pair in _BACKSLASH_DELIMITER_PAIRS:
        for match in pair.finditer(text):
            if re.search(r"[\\^_]", match.group("body")):
                delimiters += [match.start(), match.end() - 2]
    for start in sorted(delimiters):
        if _overlaps(protected, start, start + 2):
            continue
        findings.append(
            RuleFinding(
                "math/backslash-delimiter",
                "warning",
                f"Pandoc's markdown reads {text[start : start + 2]!r} as a literal "
                f"{text[start + 1]!r}, not a math delimiter; write math as `$...$` "
                "or `$$...$$`.",
                start,
                start + 2,
            )
        )
    return findings


def _is_math_symbol(char: str) -> bool:
    """A non-ASCII character that writes mathematics: `⊗`, `→`, `α`, `ℓ`, `²`, `𝔤`."""
    if char.isascii():
        return False
    code = ord(char)
    return (
        unicodedata.category(char) == "Sm"
        or 0x0370 <= code <= 0x03FF  # Greek and Coptic
        or 0x2070 <= code <= 0x209F  # Superscripts and Subscripts
        or 0x2100 <= code <= 0x214F  # Letterlike Symbols
        or 0x1D400 <= code <= 0x1D7FF  # Mathematical Alphanumeric Symbols
        or char in "²³¹"
    )


def _math_notation_findings(
    text: str, protected: bytearray, fences: list[_Fence]
) -> list[RuleFinding]:
    """
    Report mathematics written outside math mode, and Unicode math symbols.

    Outside `$...$`, pandoc reads TeX notation as Markdown: `_` delimits emphasis,
    and a bare control word such as `\\sum` is raw TeX, which HTML output drops.
    Unicode symbols render as text, not as mathematics, and fail under pdflatex,
    so they are reported everywhere -- prose, code spans and math alike -- except
    in a fenced block that declares a language, whose own syntax may use them
    (Lean's `∀` and `→`).
    """
    in_language = bytearray(len(text))
    for fence in fences:
        if fence.info:
            last = fence.closing or (
                fence.content[-1] if fence.content else fence.opening
            )
            _mark(in_language, fence.opening.start, last.raw_end)
    urls = bytearray(len(text))
    for match in _ANY_URL.finditer(text):
        _mark(urls, match.start(), match.end())
    for match in _INLINE_LINK.finditer(text):
        _mark(urls, match.start("dest"), match.end("dest"))
    # `_INLINE_LINK` does not match link text that holds brackets (`$R[[t]]$`), but
    # a `](#fragment)` destination is recognizable on its own.
    for match in _FRAGMENT_DESTINATION.finditer(text):
        _mark(urls, match.start("dest"), match.end("dest"))

    findings: list[RuleFinding] = []
    for match in _TEX_IN_PROSE.finditer(text):
        start, end = match.start(), match.end()
        if _overlaps(protected, start, end) or _overlaps(urls, start, end):
            continue
        findings.append(
            RuleFinding(
                "math/outside-math-mode",
                "warning",
                f"TeX notation {match.group(0)!r} is outside math mode; put the "
                "expression in `$...$`.",
                start,
                end,
            )
        )
    for offset, char in enumerate(text):
        if not _is_math_symbol(char) or in_language[offset] or urls[offset]:
            continue
        findings.append(
            RuleFinding(
                "math/unicode-symbol",
                "warning",
                f"Unicode math symbol {char!r} (U+{ord(char):04X}); write it as TeX "
                "inside `$...$`.",
                offset,
                offset + 1,
            )
        )
    return findings


def _target_heading_ids(path: Path) -> set[str] | None:
    try:
        target_text = path.read_text()
    except (OSError, UnicodeError):
        return None
    target_lines = _lines(target_text)
    target_frontmatter = _frontmatter(target_lines)
    return _heading_identifiers(_headings(target_text, target_lines, target_frontmatter))


def _local_destination_finding(
    destination: str,
    start: int,
    end: int,
    source_path: Path | None,
) -> RuleFinding | None:
    if source_path is None or not destination or destination.startswith("#"):
        return None
    parsed = urlsplit(destination)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    relative = Path(unquote(parsed.path))
    target = relative if relative.is_absolute() else source_path.parent / relative
    if not target.exists():
        return RuleFinding(
            "link/missing-local-target",
            "warning",
            f"Local link target {parsed.path!r} does not exist relative to this document.",
            start,
            end,
        )
    if not parsed.fragment or not target.is_file():
        return None
    if target.suffix.casefold() not in {".md", ".markdown", ".mdown", ".mkd"}:
        return None
    identifiers = _target_heading_ids(target)
    fragment = unquote(parsed.fragment)
    if identifiers is None or fragment in identifiers:
        return None
    return RuleFinding(
        "link/invalid-fragment",
        "warning",
        f"Fragment '#{fragment}' does not match a heading identifier in {parsed.path!r}.",
        start,
        end,
    )


def _link_findings(
    text: str,
    headings: list[_Heading],
    definitions: list[_Definition],
    styles: frozenset[StyleRule],
    source_path: Path | None,
    frontmatter: _Frontmatter | None,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    heading_ids = _heading_identifiers(headings)
    if frontmatter is not None and frontmatter.closing is None:
        body_start = len(text)
    else:
        body_start = frontmatter.closing.raw_end if frontmatter and frontmatter.closing else 0
    body = text[body_start:]
    starts = [body_start]
    for line in body.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    for located in located_nodes(read_source_ast(body, pandoc_executable())):
        kind = located.node.get("t")
        if kind not in {"Link", "Image"}:
            continue
        content = located.node.get("c")
        if not isinstance(content, list) or len(content) != 3:
            continue
        attributes, inlines, target = content
        if not isinstance(attributes, list) or len(attributes) != 3:
            continue
        if not isinstance(target, list) or len(target) != 2:
            continue
        destination = target[0]
        if not isinstance(destination, str):
            continue
        begin, finish = located.source_range.start, located.source_range.end
        if begin.line >= len(starts) or finish.line >= len(starts):
            continue
        start = starts[begin.line - 1] + begin.column - 1
        end = starts[finish.line - 1] + finish.column - 1
        if not (0 <= start < end <= len(text)):
            continue
        raw = text[start:end]
        is_image = kind == "Image"
        text_content = stringify_inlines(inlines).strip()
        if not destination:
            findings.append(
                RuleFinding(
                    "link/empty-destination",
                    "warning",
                    f"{'Image' if is_image else 'Link'} has an empty destination.",
                    start,
                    end,
                )
            )
        if is_image and not text_content:
            findings.append(
                RuleFinding(
                    "accessibility/image-alt",
                    "warning",
                    "Image has empty alternative text.",
                    start,
                    end,
                )
            )
        classes = attributes[1]
        autolink = isinstance(classes, list) and "uri" in classes
        if not is_image and not autolink and text_content.casefold() in _NON_DESCRIPTIVE_LINK_TEXT:
            findings.append(
                RuleFinding(
                    "link/non-descriptive-text",
                    "warning",
                    f"Link text {text_content!r} does not describe its destination.",
                    start,
                    end,
                )
            )
        if not is_image and destination.startswith("#"):
            fragment = unquote(destination[1:])
            if fragment and fragment not in heading_ids:
                findings.append(
                    RuleFinding(
                        "link/invalid-fragment",
                        "warning",
                        f"Local fragment '#{fragment}' does not match a heading identifier in this document.",
                        start,
                        end,
                    )
                )
        local_finding = _local_destination_finding(
            destination, start, end, source_path
        )
        if local_finding is not None:
            findings.append(local_finding)
        if (
            StyleRule.BARE_URL in styles
            and autolink
            and raw.startswith(("http://", "https://"))
        ):
            findings.append(
                RuleFinding(
                    "style/bare-url",
                    "warning",
                    "Bare URL is used in prose; use a descriptive Markdown link.",
                    start,
                    end,
                )
            )

    # Reference-definition destinations can also be same-document fragments.
    for definition in definitions:
        if definition.destination.startswith("#"):
            fragment = unquote(definition.destination[1:])
            if fragment and fragment not in heading_ids:
                findings.append(
                    RuleFinding(
                        "link/invalid-fragment",
                        "warning",
                        f"Local fragment '#{fragment}' does not match a heading identifier in this document.",
                        definition.line.start,
                        definition.line.end,
                    )
                )
        local_finding = _local_destination_finding(
            definition.destination,
            definition.line.start,
            definition.line.end,
            source_path,
        )
        if local_finding is not None:
            findings.append(local_finding)

    return findings


def _table_boundary_findings(
    lines: list[_Line], protected: bytearray
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    index = 0
    while index + 1 < len(lines):
        line = lines[index]
        next_line = lines[index + 1]
        if _overlaps(protected, line.start, line.end):
            index += 1
            continue
        looks_like_header = "|" in line.text and bool(line.text.strip())
        delimiter = re.match(
            r"^ {0,3}\|?(?:[ \t]*:?-{1,}:?[ \t]*\|)+[ \t]*:?-{1,}:?[ \t]*\|?[ \t]*$",
            next_line.text,
        )
        if not looks_like_header or delimiter is None:
            index += 1
            continue
        start = index
        end = index + 2
        while end < len(lines) and "|" in lines[end].text and lines[end].text.strip():
            end += 1
        if start > 0 and lines[start - 1].text.strip():
            findings.append(
                RuleFinding(
                    "table/surrounding-blank-lines",
                    "warning",
                    "Pipe table should be preceded by a blank line.",
                    line.start,
                    line.end,
                )
            )
        if end < len(lines) and lines[end].text.strip():
            last = lines[end - 1]
            findings.append(
                RuleFinding(
                    "table/surrounding-blank-lines",
                    "warning",
                    "Pipe table should be followed by a blank line.",
                    last.start,
                    last.end,
                )
            )
        index = end
    return findings


def _style_findings(
    text: str,
    lines: list[_Line],
    protected: bytearray,
    styles: frozenset[StyleRule],
    max_line_length: int | None,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    if StyleRule.UNORDERED_LIST_MARKER in styles:
        markers_by_indent: dict[int, str] = {}
        for line in lines:
            if _overlaps(protected, line.start, line.end):
                continue
            match = _UNORDERED_MARKER.match(line.text)
            if match is None:
                continue
            indent = len(match.group("indent"))
            marker = match.group("marker")
            expected = markers_by_indent.setdefault(indent, marker)
            if marker != expected:
                findings.append(
                    RuleFinding(
                        "style/unordered-list-marker",
                        "warning",
                        f"Unordered-list marker {marker!r} is inconsistent with {expected!r} at this nesting level.",
                        line.start + match.start("marker"),
                        line.start + match.end("marker"),
                    )
                )
    if StyleRule.NO_INLINE_HTML in styles:
        for match in _INLINE_HTML.finditer(text):
            if _overlaps(protected, match.start(), match.end()):
                continue
            findings.append(
                RuleFinding(
                    "style/no-inline-html",
                    "warning",
                    "Inline HTML is disallowed by the selected lint style.",
                    match.start(),
                    match.end(),
                )
            )
    if max_line_length is not None and max_line_length > 0:
        for line in lines:
            if len(line.text) <= max_line_length or _overlaps(
                protected, line.start, line.end
            ):
                continue
            findings.append(
                RuleFinding(
                    "style/line-length",
                    "warning",
                    f"Line is {len(line.text)} characters; configured maximum is {max_line_length}.",
                    line.start + max_line_length,
                    line.end,
                )
            )
    return findings


def lint_rule_findings(
    text: str,
    *,
    source_path: Path | None = None,
    styles: frozenset[StyleRule] | None = None,
    max_line_length: int | None = None,
) -> list[RuleFinding]:
    """Run explicit semantic/structural lint rules over ``text``."""
    styles = styles or frozenset()
    lines = _lines(text)
    frontmatter = _frontmatter(lines)
    frontmatter_line_numbers: set[int] = set()
    if frontmatter is not None:
        last_number = (
            frontmatter.closing.number
            if frontmatter.closing is not None
            else lines[-1].number
        )
        frontmatter_line_numbers.update(
            range(frontmatter.opening.number, last_number + 1)
        )
    fences = _fences(lines, frontmatter_line_numbers)
    protected = _build_protected_map(text, lines, frontmatter, fences)
    headings = _headings(text, lines, frontmatter)
    definitions = _definitions(lines, protected)

    findings = [
        *_frontmatter_findings(frontmatter),
        *_reference_findings(text, lines, protected),
        *_footnote_findings(text, lines, protected),
        *_heading_findings(headings, lines, protected, styles),
        *_fence_findings(fences, lines, styles),
        *_explicit_identifier_findings(text, protected),
        *_attribute_findings(lines, protected),
        *_unclosed_construct_findings(lines, protected),
        *_malformed_inline_findings(text, protected),
        *_math_notation_findings(text, protected, fences),
        *_link_findings(text, headings, definitions, styles, source_path, frontmatter),
        *_table_boundary_findings(lines, protected),
        *_style_findings(text, lines, protected, styles, max_line_length),
    ]
    # Identical findings can arise when one malformed token is recognized by a
    # generic and a family-specific scan.  Preserve the most specific first one.
    deduplicated: dict[tuple[str, int, int, str], RuleFinding] = {}
    for finding in findings:
        deduplicated.setdefault(
            (finding.rule, finding.start, finding.end, finding.message), finding
        )
    return sorted(
        deduplicated.values(),
        key=lambda finding: (finding.start, finding.end, finding.rule),
    )


__all__ = ("RuleFinding", "StyleRule", "lint_rule_findings")

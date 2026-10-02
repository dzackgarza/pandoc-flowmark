"""
Cheap checks for input that was already ambiguous before flowmark touched it.

When verification fails there are two different questions: *did flowmark break
this?* and *was this already broken?*  The pandoc gate can only ask the first, so
on input that was already broken -- a fence never closed, a `$` with no closer
-- it reports a flowmark bug that no report can fix.

So these run only when verification has already failed, and only to say "here is
something in your input that pandoc reads differently than you probably meant".
They never gate a run on their own and they never fire on a document that verified.

**Precision over recall.**  A check that fires on ordinary markdown would relabel
every real flowmark defect as the user's fault, which is worse than the message it
replaces.  Each check below is written to be quiet unless it is fairly sure, and
`test_preflight_is_quiet_on_clean_input` is what holds that line.  Missing a
malformed document costs today's message; a false positive costs the truth.  A
check must state a rule pandoc actually applies, verified against pandoc itself.

The same checks are importable on their own, so a document can be checked without
reformatting it -- problem 3 in #17 was breaking the reporter's pandoc build before
flowmark ever ran.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from flowmark.linewrapping.atomic_patterns import DOLLAR_MATH


class MalformedInputError(ValueError):
    """Raised when the document has an error flowmark will not format around."""


@dataclass(frozen=True)
class Finding:
    """One suspect construct, at the 1-based line where it appears."""

    line: int
    message: str


_FENCE = re.compile(r"^ {,3}(`{3,}|~{3,})(.*)$")


def opens_fence(line: str) -> bool:
    """
    Whether `line` is shaped like a CommonMark fence opener. A backtick fence's
    info string may not contain a backtick, which keeps an inline code span from
    opening a block.
    """
    match = _FENCE.match(line)
    return match is not None and not (
        match.group(1)[0] == "`" and "`" in match.group(2)
    )


def unclosed_fences(text: str) -> list[Finding]:
    """
    Report a fenced block that is never closed, at its opening line.

    Pandoc reads an unclosed fence and the lines after it as paragraph text, while
    CommonMark reads a code block to the end of the document. This is an error in
    the document, not something to format around: `reformat_text` refuses it.
    """
    lines = text.split("\n")
    open_at: int | None = None
    fence = ""
    for offset, line in enumerate(lines):
        match = _FENCE.match(line)
        if not match:
            continue
        if open_at is None:
            # A backtick fence's info string may not contain a backtick, which is
            # what keeps an inline code span from opening a block here.
            if match.group(1)[0] == "`" and "`" in match.group(2):
                continue
            open_at, fence = offset, match.group(1)
        elif match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            open_at = None
    if open_at is None:
        return []
    return [Finding(open_at + 1, f"fence `{fence}` is opened here and never closed")]


_NOT_MATH = re.compile(rf"`+[^`\n]*`+|\\\$|{DOLLAR_MATH}")

# A `$` pair that pandoc does not read as math only because of how it is delimited
# (pandoc manual, "Math"): whitespace just inside a delimiter, or a digit right after
# the closer. An opener followed by a digit is currency, not a delimiter.
_REJECTED_MATH = re.compile(r"\$(?![\d$])(?P<body>[^$]+?)\$(?P<digit>\d)?")


def _blank(match: re.Match[str]) -> str:
    """The match with every character but a line break replaced by a space."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _prose_paragraphs(lines: list[str]) -> list[list[int]]:
    """
    The 0-based line indices of each run of prose lines.

    A blank line or a line inside a fenced code block ends a run. `$$` display math
    is inline to pandoc: it closes at the next `$$` in the same paragraph, so its
    lines belong to the run and `DOLLAR_MATH` reads the span.
    """
    fenced = _fenced_line_numbers(lines)
    paragraphs: list[list[int]] = [[]]
    for offset, line in enumerate(lines):
        if offset in fenced or not line.strip():
            paragraphs.append([])
            continue
        paragraphs[-1].append(offset)
    return [p for p in paragraphs if p]


def _rejected_in(lines: list[str], paragraph: list[int]) -> list[Finding]:
    text = _NOT_MATH.sub(_blank, "\n".join(lines[offset] for offset in paragraph))
    findings: list[Finding] = []
    for match in _REJECTED_MATH.finditer(text):
        body = match.group("body")
        if match.group("digit"):
            reason = "a digit follows the closing `$`"
        elif body[0].isspace() or body[-1].isspace():
            reason = "there is a space just inside a `$`"
        else:
            continue
        line = paragraph[text.count("\n", 0, match.start())]
        span = " ".join(match.group(0).split())
        findings.append(
            Finding(
                line + 1,
                f"`{span}` is not math to pandoc because {reason}; "
                f"write `${' '.join(body.split())}$` if it is math",
            )
        )
    return findings


def rejected_math(text: str) -> list[Finding]:
    """
    Report each `$...$` that is meant as math but that pandoc reads as text.

    Pandoc reads `$ x $` and `$x$1` as prose, so any TeX inside them becomes
    emphasis or plain text. This is an error in the document, not something to
    format around: `reformat_text` refuses a document with one.
    """
    lines = text.split("\n")
    return [f for p in _prose_paragraphs(lines) for f in _rejected_in(lines, p)]


def _stray_dollars(lines: list[str], paragraph: list[int]) -> list[Finding]:
    """
    Lines of `paragraph` holding a `$` that pandoc reads as no math span.

    The paragraph is matched as a whole, because inline math may continue onto the
    next line. Code spans, escaped `\\$`, every span pandoc's rule (`DOLLAR_MATH`)
    reads as math, and the pairs `rejected_math` reports are blanked first; a `$`
    left before a digit is currency.
    """
    text = "\n".join(lines[offset] for offset in paragraph)
    bare = _REJECTED_MATH.sub(_blank, _NOT_MATH.sub(_blank, text))
    stray = {
        paragraph[bare.count("\n", 0, match.start())]
        for match in re.finditer(r"\$(?!\d)", bare)
    }
    return [
        Finding(offset + 1, "unterminated `$` math delimiter on this line")
        for offset in sorted(stray)
    ]


def _math_findings(lines: list[str]) -> list[Finding]:
    """Report a `$` that opens no math span pandoc would read."""
    return [f for p in _prose_paragraphs(lines) for f in _stray_dollars(lines, p)]


def _fenced_line_numbers(lines: list[str]) -> frozenset[int]:
    """0-based indices of lines inside a fenced code block, delimiters included."""
    inside: set[int] = set()
    fence = ""
    for offset, line in enumerate(lines):
        match = _FENCE.match(line)
        if not fence:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = match.group(1)
                inside.add(offset)
            continue
        inside.add(offset)
        if (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
        ):
            fence = ""
    return frozenset(inside)


def preflight(text: str) -> list[Finding]:
    """
    Report constructs in `text` that pandoc probably reads differently than intended.

    Ordered by line. An empty list means nothing suspect was found -- which is not a
    claim that the document is well-formed, only that these checks had nothing to
    say about it.
    """
    lines = text.split("\n")
    findings = [
        *unclosed_fences(text),
        *rejected_math(text),
        *_math_findings(lines),
    ]
    return sorted(findings, key=lambda finding: finding.line)

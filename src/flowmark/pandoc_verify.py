"""
Verify that reformatting did not *unintentionally* change what pandoc reads.

Flowmark normalizes documents to an opinionated style, so full AST preservation
is not the contract and cannot be: some normalizations deliberately change the
parsed AST.  A bold heading is stripped because the `<h1>`/`\\section` element
should carry that weight rather than hand-applied bolding, and list spacing is
standardized rather than letting each document control presentation.  Deliberate
style changes like these are the formatter doing its job, so they never gate a
run; they are at most *reported* (a stderr note when one applies without having
been asked for); see `_NORMALIZATIONS`.

Every *other* AST change is data being destroyed by accident -- a construct the
formatter mishandles rather than an opinion it holds -- and that is what this
check exists to catch.  Pandoc owns the definition of what these documents say,
so it is the authority to ask, rather than re-deriving its grammar here and
hoping.

Some of flowmark's deliberate spelling changes are already invisible to pandoc's
reader, so they need no special handling here: indented and fenced code blocks
both read as `CodeBlock`, and footnote definitions read the same with or without a
blank line between them.

`smartquotes` is *not* one of them, contrary to what this module assumed until
issue #27.  Pandoc's `smart` extension does not fold straight and curly quotes
into the same reading: `"hi"` is `Quoted DoubleQuote [Str "hi"]` and `“hi”` is a
literal `Str` (checked against pandoc 3.9.0.2).  Curling a quote is therefore a
real AST change and needs a declared normalization like any other opinion; see
`SMART_QUOTES`.

Two do need canonicalizing before comparison, both concerning inline whitespace:

- Rewrapping a paragraph turns a `Space` into a `SoftBreak` and back.  This is
  flowmark's whole purpose, so comparing them strictly would reject every run.
- `ellipses` inserts a space on *either side* of the ellipsis it creates
  (`word...word` -> `word … word`), per the rule documented on
  `flowmark.typography.ellipses.ellipses`, which owns that contract.

So inline text is compared as a whitespace-normalized stream rather than
token-for-token, and whitespace adjacent to an ellipsis is dropped on both
sides.  Whitespace *between* inlines is spelling; everything else -- every
element type, every nesting relationship, and the text itself -- is compared
exactly, which is what catches a `Str` becoming an `Emph` or a `Div` shedding
its contents.

The ellipsis rule is owned by `typography/ellipses.py`, not here.  This module
only declares spacing around an ellipsis to be out of scope; if that contract
changes, `test_pandoc_verify.py` drives its cases from `ellipses()` itself, so
the divergence surfaces as a failing test rather than silently.

This needs the `pandoc` binary on PATH.  It is not a fallback that quietly
degrades when pandoc is missing: callers asking to verify get an error.
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from itertools import combinations
from typing import cast

from flowmark.formats.frontmatter import split_frontmatter
from flowmark.linewrapping.tag_handling import is_tag_only_line

PandocJson = (
    str | int | float | bool | None | list["PandocJson"] | dict[str, "PandocJson"]
)
"""One node of pandoc's JSON AST, exactly as `json.loads` produces it."""

PANDOC_FORMAT = (
    "markdown+fenced_divs+raw_tex+tex_math_dollars"
    "+tex_math_single_backslash+wikilinks_title_after_pipe"
)
"""The Pandoc Markdown dialect used by the document writing pipeline."""


class PandocUnavailableError(RuntimeError):
    """Raised when verification is requested but the pandoc binary is not on PATH."""


class PandocParseError(ValueError):
    """
    Raised when pandoc is present but rejects the document.

    Distinct from `PandocUnavailableError`: pandoc ran and did its job.  Conflating
    the two would report a malformed document as a missing install.
    """


class MeaningChangedError(ValueError):
    """
    Raised when reformatting changed the document's parsed AST.

    `detail` is the machine-readable half of the message (`"block 7: Para ->
    Header"`), kept separately so a caller that learns something further about the
    document -- that the input was already ambiguous, say -- can rebuild the message
    around it rather than parsing it back out of prose.
    """

    detail: str
    block: int | None
    """The 0-based top-level block of the source that pandoc reads differently."""

    def __init__(
        self, message: str, detail: str = "", block: int | None = None
    ) -> None:
        super().__init__(message)
        self.detail = detail
        self.block = block


def _pandoc_exe() -> str:
    pandoc_exe = shutil.which("pandoc")
    if pandoc_exe is None:
        raise PandocUnavailableError(
            "Verification requires the `pandoc` binary on PATH. Install pandoc (https://pandoc.org/installing.html) or drop --verify."
        )
    return pandoc_exe


def _spawn_pandoc(pandoc_exe: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [pandoc_exe, "-f", PANDOC_FORMAT, "-t", "json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _collect_blocks(
    proc: subprocess.Popen[str], markdown_text: str
) -> list[PandocJson]:
    # YAML frontmatter is document metadata, not body. The formatter (via the same
    # split_frontmatter) preserves it verbatim, so it can never be the source of a
    # meaning change; and it is frequently lax YAML that pandoc's metadata reader
    # rejects outright (a Cursor/agent-rule `globs: *.py` reads as a YAML alias and
    # aborts the parse). Compare the body only, so frontmatter never reaches pandoc.
    _frontmatter, content = split_frontmatter(markdown_text)
    stdout, stderr = proc.communicate(content)
    if proc.returncode != 0:
        raise PandocParseError(f"pandoc could not parse the document: {stderr.strip()}")
    blocks: list[PandocJson] = json.loads(stdout)["blocks"]
    return blocks


def pandoc_ast(markdown_text: str) -> list[PandocJson]:
    """
    Return pandoc's parsed block list for `markdown_text`.

    Raises:
        PandocUnavailableError: if the pandoc binary is not on PATH.
        PandocParseError: if pandoc ran but could not parse the document.
    """
    return _collect_blocks(_spawn_pandoc(_pandoc_exe()), markdown_text)


def block_indices(markdown_text: str, lines: list[int]) -> list[int]:
    """
    The 0-based top-level block that each 1-based line of `markdown_text` is in.

    Pandoc's `markdown` reader records no source positions, so a line's block is
    found by parsing the text up to and including that line: the line belongs to
    the last block of that prefix. The prefixes parse concurrently, a few at a time.
    """
    text_lines = markdown_text.split("\n")
    pandoc_exe = _pandoc_exe()
    batch = os.cpu_count() or 1
    indices: list[int] = []
    for start in range(0, len(lines), batch):
        running = [
            (_spawn_pandoc(pandoc_exe), "\n".join(text_lines[:line]) + "\n")
            for line in lines[start : start + batch]
        ]
        indices += [len(_collect_blocks(proc, prefix)) - 1 for proc, prefix in running]
    return indices


def _pandoc_ast_pair(
    source: str, result: str
) -> tuple[list[PandocJson], list[PandocJson]]:
    """
    Parse both documents with two concurrent pandoc processes.

    A comparison always needs both trees, and pandoc's startup dominates the
    cost, so overlapping the two runs roughly halves verification latency.
    """
    pandoc_exe = _pandoc_exe()
    source_proc = _spawn_pandoc(pandoc_exe)
    result_proc = _spawn_pandoc(pandoc_exe)
    try:
        source_blocks = _collect_blocks(source_proc, source)
    except Exception:
        result_proc.kill()
        result_proc.communicate()
        raise
    return source_blocks, _collect_blocks(result_proc, result)


_SPACE_INLINES = frozenset({"Space", "SoftBreak"})


def _normalize_text(text: str) -> str:
    """
    Collapse whitespace runs, and drop whitespace on either side of an ellipsis.

    `ellipses` inserts a space on *both* sides (`word...word` -> `word … word`),
    so stripping only the leading one makes this fire on valid output.  That rule
    is owned by `flowmark.typography.ellipses`; this only declares spacing around
    an ellipsis out of scope for the comparison.
    """
    return re.sub(r"\s*…\s*", "…", re.sub(r"\s+", " ", text))


_TEX_COMMENT = re.compile(r"(?<!\\)%")


def _canonical_math(node: dict[str, PandocJson]) -> PandocJson:
    """
    Collapse whitespace runs in a `Math` node's TeX to one space.

    TeX reads the end of a line as a space and a run of spaces as one (The TeXbook,
    chapter 8), so `a +\\nb` and `a + b` are the same formula; rewrapping a paragraph
    turns the first into the second. A `%` comment is the exception -- it runs to
    the end of the line, so joining that line would comment out what follows -- and
    such a node is compared exactly.
    """
    kind, tex = cast("list[PandocJson]", node["c"])
    if not isinstance(tex, str) or _TEX_COMMENT.search(tex):
        return node
    return {"t": "Math", "c": [kind, re.sub(r"\s+", " ", tex)]}


def _canonical(node: PandocJson) -> PandocJson:
    """
    Rewrite `node` so that inline whitespace differences compare equal.

    Runs of `Str`/`Space`/`SoftBreak` collapse into a single normalized `Str`.
    Anything else is structure and is preserved exactly, so this cannot hide a
    changed element type, a changed nesting, or changed words.
    """
    if isinstance(node, dict):
        if node.get("t") == "Math":
            return _canonical_math(node)
        return {key: _canonical(value) for key, value in node.items()}
    if not isinstance(node, list):
        return node

    out: list[PandocJson] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = _normalize_text("".join(buffer))
        buffer.clear()
        if text:
            out.append({"t": "Str", "c": text})

    for item in node:
        if isinstance(item, dict):
            kind = item.get("t")
            if kind in _SPACE_INLINES:
                buffer.append(" ")
                continue
            if kind == "Str":
                buffer.append(str(item.get("c", "")))
                continue
        flush()
        out.append(_canonical(item))
    flush()
    return out


def _unbold_headings(node: PandocJson) -> PandocJson:
    """
    Unwrap a heading whose entire content is bold, on both sides of a comparison.

    Flowmark's `cleanups` deliberately strips this: a heading's weight is the
    `<h1>`/`\\section` element's job, and bolding it by hand adds nothing but
    non-uniformity.  So `Header[Strong[x]]` -> `Header[x]` is a normalization,
    not a loss -- but it *is* an AST change, so it is reported as one.
    """
    if isinstance(node, dict):
        out = {key: _unbold_headings(value) for key, value in node.items()}
        if out.get("t") == "Header":
            content = out.get("c")
            if isinstance(content, list) and len(content) == 3:
                inlines = content[2]
                if isinstance(inlines, list) and len(inlines) == 1:
                    only = inlines[0]
                    if isinstance(only, dict) and only.get("t") == "Strong":
                        out["c"] = [content[0], content[1], only.get("c")]
        return out
    if isinstance(node, list):
        return [_unbold_headings(item) for item in node]
    return node


def _plain_to_para(node: PandocJson) -> PandocJson:
    """
    Treat `Plain` and `Para` as one, on both sides of a comparison.

    This is the only thing that distinguishes a tight list from a loose one, and
    standardizing list spacing is what `list_spacing` is for.  Same story as
    headings: a deliberate normalization that shows up as an AST change.
    """
    if isinstance(node, dict):
        out = {key: _plain_to_para(value) for key, value in node.items()}
        if out.get("t") == "Plain":
            out["t"] = "Para"
        return out
    if isinstance(node, list):
        return [_plain_to_para(item) for item in node]
    return node


_QUOTE_MARKS = {"DoubleQuote": ("“", "”"), "SingleQuote": ("‘", "’")}


def _flatten_quoted(node: PandocJson) -> PandocJson:
    """
    Replace a `Quoted` span with its curled spelling, on both sides of a comparison.

    Pandoc's `smart` extension does *not* fold straight and curly quotes into the
    same reading, contrary to what this module long assumed: a straight `"hi"` is
    `Quoted DoubleQuote [Str "hi"]`, while the curled `“hi”` is a literal
    `Str`.  So curling a quote -- which is all `smartquotes` does -- genuinely
    changes the AST, and without this entry the released `--smartquotes` flag is
    refused by the gate on any document containing a straight quote.

    Writing the marks *into* the stream, rather than dropping the `Quoted` wrapper,
    is what keeps this narrow: a quote that was deleted or whose pairing moved
    lands its marks somewhere else in the text and still mismatches.
    """
    if isinstance(node, dict):
        return {key: _flatten_quoted(value) for key, value in node.items()}
    if not isinstance(node, list):
        return node
    return _flatten_quoted_list(node)


def _flatten_quoted_list(items: list[PandocJson]) -> list[PandocJson]:
    """The inline-run half of `_flatten_quoted`, split out to keep the list type."""
    out: list[PandocJson] = []
    for item in items:
        quoted = _as_quoted(item)
        if quoted is None:
            out.append(_flatten_quoted(item))
            continue
        (open_mark, close_mark), inner = quoted
        out.append({"t": "Str", "c": open_mark})
        out.extend(_flatten_quoted_list(inner))
        out.append({"t": "Str", "c": close_mark})
    return out


def _as_quoted(item: PandocJson) -> tuple[tuple[str, str], list[PandocJson]] | None:
    """Return `((open, close), inlines)` if `item` is a `Quoted` span, else None."""
    if not isinstance(item, dict) or item.get("t") != "Quoted":
        return None
    content = item.get("c")
    if not isinstance(content, list) or len(content) != 2:
        return None
    kind, inlines = content
    if not isinstance(kind, dict) or not isinstance(inlines, list):
        return None
    marks = _QUOTE_MARKS.get(str(kind.get("t")))
    return None if marks is None else (marks, inlines)


_BULLET_MARKERS = ("-", "*", "+")
"""The three CommonMark bullet characters.

Pandoc's `BulletList` does not record which one the source used, so reconstructing
the paragraph a list came from means trying each.  The rest of the text has to
match exactly either way, so a wrong guess simply fails to reconcile.
"""

_PARAGRAPH_BLOCKS = frozenset({"Para", "Plain"})


def _list_items_and_markers(
    list_block: dict[str, PandocJson],
) -> tuple[list[PandocJson], list[list[str]]]:
    """
    The items of `list_block`, and the candidate marker spellings for them.

    Ordered lists are exact -- pandoc records the start number and the delimiter --
    so they yield a single candidate.  Bullet lists yield one per bullet character.
    A block that is not a list yields no items and no candidates.
    """
    kind = list_block.get("t")
    content = list_block.get("c")

    if kind == "BulletList" and isinstance(content, list):
        return content, [[marker] * len(content) for marker in _BULLET_MARKERS]

    if kind == "OrderedList" and isinstance(content, list) and len(content) == 2:
        attrs, ordered_items = content
        if (
            not isinstance(attrs, list)
            or len(attrs) != 3
            or not isinstance(ordered_items, list)
        ):
            return [], []
        start, delim = attrs[0], attrs[2]
        first = start if isinstance(start, int) else 1
        suffix = (
            ")" if isinstance(delim, dict) and delim.get("t") == "OneParen" else "."
        )
        return ordered_items, [
            [f"{first + offset}{suffix}" for offset in range(len(ordered_items))]
        ]

    return [], []


def _flatten_list_into_paragraph(
    para: dict[str, PandocJson], list_block: dict[str, PandocJson]
) -> list[list[PandocJson]]:
    """
    Spell `para` followed by `list_block` back out as one paragraph's inlines, once
    per candidate marker set.

    Each marker is emitted surrounded by `Space`, which is what a line join between
    the paragraph and the marker actually produces.  An item may hold paragraphs
    and nested lists: an indented sub-bullet under a lazy line is more of the same
    paragraph to pandoc.  Any other block is refused, since a lazy continuation
    cannot produce it.  One bullet character is tried for the whole nest.
    """
    para_inlines = para.get("c")
    if not isinstance(para_inlines, list):
        return []
    candidates: list[list[PandocJson]] = []
    for bullet in _BULLET_MARKERS:
        flat = _flatten_list(list_block, bullet)
        if flat is not None and [*para_inlines, *flat] not in candidates:
            candidates.append([*para_inlines, *flat])
    return candidates


def _flatten_list(
    list_block: dict[str, PandocJson], bullet: str
) -> list[PandocJson] | None:
    """`list_block` spelled as the inlines of lazy paragraph lines, or None."""
    items, marker_candidates = _list_items_and_markers(list_block)
    if not marker_candidates:
        return None
    markers = (
        [bullet] * len(items)
        if list_block.get("t") == "BulletList"
        else marker_candidates[0]
    )
    flat: list[PandocJson] = []
    for marker, item in zip(markers, items, strict=False):
        if not isinstance(item, list):
            return None
        flat.append({"t": "Space"})
        flat.append({"t": "Str", "c": marker})
        for inner in item:
            if not isinstance(inner, dict):
                return None
            inner_inlines = inner.get("c")
            if inner.get("t") in _PARAGRAPH_BLOCKS and isinstance(inner_inlines, list):
                flat.append({"t": "Space"})
                flat.extend(inner_inlines)
                continue
            nested = _flatten_list(inner, bullet)
            if nested is None:
                return None
            flat += nested
    return flat


def _collapse_lazy_lists_at_level(
    before: list[PandocJson], after: list[PandocJson]
) -> list[PandocJson]:
    """
    Rewrite `after` so a paragraph that grew a list beside it becomes the single
    paragraph `before` has there -- but only when flattening reproduces `before`
    exactly.
    """
    out: list[PandocJson] = []
    before_index = 0
    after_index = 0
    while after_index < len(after):
        para = after[after_index]
        follows = after[after_index + 1] if after_index + 1 < len(after) else None
        original = before[before_index] if before_index < len(before) else None
        if (
            isinstance(original, dict)
            and isinstance(para, dict)
            and isinstance(follows, dict)
            and original.get("t") in _PARAGRAPH_BLOCKS
            and para.get("t") in _PARAGRAPH_BLOCKS
            and any(
                _canonical(flat) == _canonical(original.get("c"))
                for flat in _flatten_list_into_paragraph(para, follows)
            )
        ):
            out.append(original)
            after_index += 2
            before_index += 1
            continue
        out.append(para)
        after_index += 1
        before_index += 1
    return out


def _tag_line_inlines(block: PandocJson) -> list[PandocJson] | None:
    """
    The inlines `block` becomes when pandoc reads it as a lazy continuation line,
    or None if `block` is not a tag-only line (`is_tag_only_line`).

    An HTML comment alone on its line reads as a `RawBlock`; a Markdoc or Jinja tag
    reads as a paragraph of text, which `_canonical` has merged into one `Str`.
    """
    if not isinstance(block, dict):
        return None
    content = block.get("c")
    if block.get("t") == "RawBlock" and isinstance(content, list):
        fmt, text = content
        if isinstance(fmt, str) and isinstance(text, str) and is_tag_only_line(text):
            return [{"t": "RawInline", "c": [fmt, text]}]
        return None
    if block.get("t") in _PARAGRAPH_BLOCKS and isinstance(content, list):
        match content:
            case [{"t": "Str", "c": str(text)}] if is_tag_only_line(text):
                return content
            case _:
                return None
    return None


def _extend_last_paragraph(
    block: PandocJson, inlines: list[PandocJson]
) -> PandocJson | None:
    """
    `block` with `inlines` appended to its last paragraph, as a lazy continuation
    line appends to the innermost open paragraph; None if `block` does not end in one.
    """
    if not isinstance(block, dict):
        return None
    kind, content = block.get("t"), block.get("c")
    if kind in _PARAGRAPH_BLOCKS and isinstance(content, list):
        paragraph: dict[str, PandocJson] = {
            "t": kind,
            "c": [*content, {"t": "Space"}, *inlines],
        }
        return paragraph
    items = content
    if kind == "OrderedList" and isinstance(content, list) and len(content) == 2:
        items = content[1]
    elif kind != "BulletList":
        return None
    if not isinstance(items, list) or not items or not isinstance(items[-1], list):
        return None
    last_item = items[-1]
    if not last_item:
        return None
    extended = _extend_last_paragraph(last_item[-1], inlines)
    if extended is None:
        return None
    new_items: list[PandocJson] = [*items[:-1], [*last_item[:-1], extended]]
    rebuilt: dict[str, PandocJson] = {"t": kind, "c": new_items}
    if kind == "OrderedList" and isinstance(content, list):
        rebuilt["c"] = [content[0], new_items]
    return rebuilt


def _fold_tag_lines_at_level(
    before: list[PandocJson], after: list[PandocJson]
) -> list[PandocJson]:
    """
    Rewrite `after` so a list followed by tag-only blocks becomes the single list
    `before` has there -- but only when folding them back into the list's last
    paragraph reproduces `before` exactly.

    The list may itself be one that flowmark made out of a lazy paragraph
    continuation (`{% field %}` / `- a` / `{% /field %}` is one paragraph to
    pandoc). Then `before` has a paragraph where `after` has a paragraph and the
    list, and the fold is accepted when flattening the folded list into that
    paragraph reproduces it. The flattening itself stays `LAZY_LIST`'s, which runs
    after this, so both opinions are reported.
    """
    out: list[PandocJson] = []
    before_index = 0
    after_index = 0
    while after_index < len(after):
        original = before[before_index] if before_index < len(before) else None
        block = after[after_index]
        folded, candidate = _fold_tag_run(
            after, after_index, lambda folded: _canonical(folded) == original
        )
        if folded:
            out.append(original)
            after_index += 1 + folded
            before_index += 1
            continue
        if (
            isinstance(block, dict)
            and isinstance(original, dict)
            and block.get("t") in _PARAGRAPH_BLOCKS
            and original.get("t") in _PARAGRAPH_BLOCKS
        ):
            para = block
            wanted = _canonical(original.get("c"))

            def flattens_to_original(list_block: PandocJson) -> bool:
                return isinstance(list_block, dict) and any(
                    _canonical(flat) == wanted
                    for flat in _flatten_list_into_paragraph(para, list_block)
                )

            folded, candidate = _fold_tag_run(
                after, after_index + 1, flattens_to_original
            )
            if folded:
                out += [block, _canonical(candidate)]
                after_index += 2 + folded
                before_index += 1
                continue
        out.append(block)
        after_index += 1
        before_index += 1
    return out


def _fold_tag_run(
    after: list[PandocJson], start: int, accept: Callable[[PandocJson], bool]
) -> tuple[int, PandocJson]:
    """
    Fold the tag-only blocks after `after[start]` into its last paragraph, one at a
    time, and return the longest fold `accept` takes (0 and None for none).
    """
    if start >= len(after):
        return 0, None
    candidate: PandocJson | None = after[start]
    best: tuple[int, PandocJson] = (0, None)
    for offset, block in enumerate(after[start + 1 :], start=1):
        inlines = _tag_line_inlines(block)
        if inlines is None or candidate is None:
            break
        candidate = _extend_last_paragraph(candidate, inlines)
        if candidate is not None and accept(candidate):
            best = (offset, candidate)
    return best


def _walk_levels(
    at_level: Callable[[list[PandocJson], list[PandocJson]], list[PandocJson]],
    before: PandocJson,
    after: PandocJson,
) -> PandocJson:
    """Walk both trees in parallel, rewriting each of `after`'s node lists with `at_level`."""
    if isinstance(before, dict) and isinstance(after, dict):
        return {
            key: _walk_levels(at_level, before.get(key), value)
            for key, value in after.items()
        }
    if isinstance(before, list) and isinstance(after, list):
        rewritten = at_level(before, after)
        return [
            _walk_levels(at_level, before[index] if index < len(before) else None, item)
            for index, item in enumerate(rewritten)
        ]
    return after


UNBOLD_HEADING = "unbold_heading"
"""Identifier for the heading-unbolding normalization; requested by `cleanups`."""

LIST_SPACING = "list_spacing"
"""Identifier for the tight/loose list normalization; requested by `list_spacing`."""

SMART_QUOTES = "smart_quotes"
"""Identifier for the quote-curling normalization; requested by `smartquotes`."""

HYPHEN_JOIN = "hyphen_join"
"""Identifier for closing up a line break that fell after a hyphen; `cleanups`."""

LAZY_LIST = "lazy_list"
"""Identifier for materializing a list out of a lazy paragraph continuation.

Never requested: no flag asks for it, so it is always reported.  The author wrote
bullets under a paragraph line and pandoc's dialect read them as prose; flowmark
gives them the list they drew, and says so.
"""

TAG_LINE_SPLIT = "tag_line_split"
"""Identifier for moving a tag-only line out of the list item above it.

Never requested, so it is always reported.  An HTML comment or Markdoc tag on the
line right after a list item is that item's text to pandoc and a block of its own
to CommonMark.  The block is what a `<!--toc:end-->` marker or a closing
`{% /tag %}` means, so flowmark writes it as one (`preprocess_tag_block_spacing`).
"""

Normalization = Callable[[PandocJson, PandocJson], tuple[PandocJson, PandocJson]]
"""A declared normalization: rewrites the two canonicalized trees so the change it
declares compares equal, and returns them.

Taking *both* trees rather than one node is what lets an entry be **directional**.
Most opinions are symmetric -- unbolding a heading means the same thing whichever
side it is seen on -- and `_both` lifts a plain node transform into this shape.
But an entry may need to accept a change in one direction while still refusing its
reverse, and a single-node transform cannot express that: rewriting both sides the
same way necessarily accepts the corruption that undoes the opinion.
"""


def _both(node_transform: Callable[[PandocJson], PandocJson]) -> Normalization:
    """Lift a symmetric node transform into a `Normalization` over both trees."""

    def normalize(
        before: PandocJson, after: PandocJson
    ) -> tuple[PandocJson, PandocJson]:
        return node_transform(before), node_transform(after)

    return normalize


def _normalize_quotes(
    before: PandocJson, after: PandocJson
) -> tuple[PandocJson, PandocJson]:
    """
    Flatten `Quoted` spans on both sides, then re-canonicalize.

    The re-canonicalization is not optional: `_flatten_quoted` emits each quote
    mark as its own `Str`, and the side that was *already* curled carries the mark
    inside a neighbouring `Str` (`Str "“a"`).  Only after the `Str` run is merged
    again do the two spell the same thing.
    """
    return _canonical(_flatten_quoted(before)), _canonical(_flatten_quoted(after))


_SUSPENSION_WORDS = frozenset({"and", "or", "to", "nor", "but", "through", "versus"})
"""Mirrors `transforms.doc_cleanups._SUSPENSION_WORDS`.

The two must agree: this decides what the gate will accept, that decides what the
formatter does, and a rule the formatter applies but the gate refuses is a document
that cannot be written.  `test_hyphen_join_scope_matches_the_cleanup` pins them.
"""


def _joinable_space(text: str, start: int, followed: bool) -> bool:
    """
    Whether the space at `text[start]`, right after a hyphen, is in #18's scope.

    `followed` says whether an inline comes after this `Str`: a trailing `- `
    closes up only against one (`degree- ` then a `Math`), never at the end of a
    paragraph.
    """
    rest = text[start:].lstrip()
    if not rest:
        return followed
    if rest.split()[0].strip(".,;:!?").lower() in _SUSPENSION_WORDS:
        return False
    return rest[0].isdigit() or rest[0].islower()


def _join_hyphen_text_toward(text: str, target: str, followed: bool) -> str:
    """
    `target` if it is `text` with some in-scope hyphen spaces closed up, else `text`.

    The formatter joins only at line breaks, so a paragraph can hold a joined
    `semi-log-scale` beside an authored `post- cases`. Which spaces were joined is
    read off `target`; every other difference leaves `text` as it was, and the
    comparison fails.
    """
    i = j = 0
    while i < len(text):
        if j < len(target) and text[i] == target[j]:
            i += 1
            j += 1
        elif (
            text[i] == " "
            and text[i - 1 : i] == "-"
            and _joinable_space(text, i, followed)
        ):
            i += 1
        else:
            return text
    return target if j == len(target) else text


def _join_hyphens_toward(node: PandocJson, target: PandocJson) -> PandocJson:
    """
    Apply #18's join to canonicalized `node` wherever `target` applied it.

    Two shapes, because `_canonical` has already merged `Str`/`Space` runs:
    the join is inside a single `Str` (`degree- 2`), or the `Str` ends with the
    hyphen and a space and the next inline is structure (`degree- ` followed by a
    `Math`, from `degree-` / `$4$`). Trees that differ in shape are left alone.
    """
    if (
        isinstance(node, dict)
        and isinstance(target, dict)
        and node.keys() == target.keys()
    ):
        return {
            key: _join_hyphens_toward(value, target[key]) for key, value in node.items()
        }
    if not (
        isinstance(node, list) and isinstance(target, list) and len(node) == len(target)
    ):
        return node

    out: list[PandocJson] = []
    for index, (item, goal) in enumerate(zip(node, target, strict=True)):
        if (
            isinstance(item, dict)
            and isinstance(goal, dict)
            and item.get("t") == goal.get("t") == "Str"
        ):
            text = _join_hyphen_text_toward(
                str(item.get("c", "")), str(goal.get("c", "")), index + 1 < len(node)
            )
            out.append({"t": "Str", "c": text})
            continue
        out.append(_join_hyphens_toward(item, goal))
    return out


def _normalize_hyphen_join(
    before: PandocJson, after: PandocJson
) -> tuple[PandocJson, PandocJson]:
    """
    Close up `before`'s hyphen-and-space so it matches a result that joined it.

    Directional, like `LAZY_LIST` and for the same reason. Only the *unjoined* side
    is rewritten, so the reverse -- a `degree-2` that came apart into `degree- 2` --
    is never reconciled and still raises. That reverse is not hypothetical: it is
    the exact damage #18 says wrapping tools inflict, and the whole reason this
    cleanup exists.
    """
    return _canonical(_join_hyphens_toward(before, after)), after


def _normalize_lazy_list(
    before: PandocJson, after: PandocJson
) -> tuple[PandocJson, PandocJson]:
    """
    Collapse lists `after` materialized out of `before`'s lazy continuations.

    Directional on purpose, and this is the entry the `Normalization` pair shape
    exists for.  Only `after` is rewritten, and only where flattening the list back
    into the paragraph reproduces `before` exactly.  The reverse -- a real list the
    formatter destroyed into prose -- has the extra structure on the `before` side,
    where nothing rewrites it, so it still mismatches and still raises.
    """
    return before, _walk_levels(_collapse_lazy_lists_at_level, before, after)


def _normalize_tag_line_split(
    before: PandocJson, after: PandocJson
) -> tuple[PandocJson, PandocJson]:
    """
    Fold tag-only blocks `after` split out of `before`'s last list item back in.

    Directional, like `LAZY_LIST`: only `after` is rewritten, and only where the
    fold reproduces `before` exactly, so a tag line the formatter pulled *into* a
    list item still raises.
    """
    return before, _walk_levels(_fold_tag_lines_at_level, before, after)


_NORMALIZATIONS: list[tuple[str, str, Normalization]] = [
    (UNBOLD_HEADING, "removed bold from a heading", _both(_unbold_headings)),
    (LIST_SPACING, "changed list spacing (tight/loose)", _both(_plain_to_para)),
    (SMART_QUOTES, "curled straight quotes", _normalize_quotes),
    # Before LAZY_LIST: a tag split out of a list that was itself a lazy
    # continuation must be folded back before the list can be flattened.
    (
        TAG_LINE_SPLIT,
        "moved a comment or tag line out of the list item above it",
        _normalize_tag_line_split,
    ),
    (
        LAZY_LIST,
        "made a list out of a lazy paragraph continuation",
        _normalize_lazy_list,
    ),
    (
        HYPHEN_JOIN,
        "closed up a line break that fell after a hyphen",
        _normalize_hyphen_join,
    ),
]
"""Flowmark's intentional, opinionated style normalizations.

These genuinely change the parsed AST, so full AST preservation is not the
contract.  They are not meaning being lost -- they are the formatter doing its
job -- so they are allowed.  Every *other* AST change is a bug and raises.

Callers are told which ones applied so they can report the ones the caller did
not ask for: unbolding a heading is unremarkable under `cleanups` and worth
saying out loud without it.

## What an entry may claim

An entry declares *one* opinion the formatter holds about spelling, named by its
identifier and described in the second field for the user-facing report.  It may
not stand in for a family of changes, and it may not be widened to make an
unrelated failure pass: the question an entry answers is "did flowmark do the
specific thing this opinion describes?", never "is this difference tolerable?".

## How narrowly it must be scoped

An entry is admissible only if the gate is no weaker for its presence.  Concretely,
the rewrite must not make a *corruption* of the same shape compare equal.  That is
the whole reason `Normalization` sees both trees: an opinion that *materializes*
structure must rewrite only the side that gained it, and only when the rewrite
reproduces the other side exactly, so the reverse -- structure the formatter
destroyed -- still mismatches and still raises.  A symmetric rewrite of both sides
cannot make that distinction, and `_both` is therefore only for opinions where the
reverse is not a corruption worth catching.

## What proof it owes

Every entry carries, in `tests/test_pandoc_verify.py`'s `NORMALIZATION_CONTRACT`
table, both:

- a **positive case**: a source/result pair this entry must accept, attributed to
  this entry and no other; and
- a **negative case**: a *nearby* source/result pair -- the same construct, the
  same shape -- that must still raise `MeaningChangedError`.

`test_every_normalization_declares_its_contract` asserts the table covers
`_NORMALIZATIONS` exactly, so an entry cannot be added without both cases.
"""


def describe(normalization: str) -> str:
    """Human-readable text for a normalization identifier."""
    return next(text for key, text, _ in _NORMALIZATIONS if key == normalization)


def _block_type(block: PandocJson) -> str:
    """The `t` tag of an AST block, for the difference report."""
    return str(block.get("t", "?")) if isinstance(block, dict) else "?"


def _first_difference(
    before: list[PandocJson], after: list[PandocJson]
) -> tuple[int, str]:
    """
    Locate the first block the two documents disagree about: its index, and a
    description of what changed.

    The whole block list used to be printed on both sides.  That is unbounded in
    the document's size, and flowmark runs inside a `pre-commit` gate where the
    output lands once per failing file -- one mid-size document produced a
    2053-character warning, which buries every other finding in the run.  A reader
    needs to know *which* block and *what changed about it*; the surrounding blocks
    that matched are noise.
    """
    # `strict=False` is the point rather than an oversight: a document that gained
    # or lost a block is exactly the case this has to describe, and the length
    # difference is reported below once the common prefix is known to match.
    for index, (before_block, after_block) in enumerate(
        zip(before, after, strict=False)
    ):
        if before_block == after_block:
            continue
        before_type, after_type = _block_type(before_block), _block_type(after_block)
        if before_type == after_type:
            return index, f"block {index}: {before_type} content differs"
        return index, f"block {index}: {before_type} -> {after_type}"

    # Every block they have in common matched, so the documents differ in length:
    # one gained or lost trailing blocks.
    index = min(len(before), len(after))
    counts = f"{len(after)} blocks vs {len(before)}"
    if len(after) > len(before):
        return index, (
            f"block {index}: (absent) -> {_block_type(after[index])}, {counts}"
        )
    return index, f"block {index}: {_block_type(before[index])} -> (absent), {counts}"


def check_meaning_preserved(
    source: str, result: str, label: str = "input"
) -> list[str]:
    """
    Check that `result` means what `source` did, allowing flowmark's intentional
    style normalizations.

    Returns the normalizations that were needed to reconcile the two, so the
    caller can report them; an empty list means the ASTs matched outright.

    Raises:
        MeaningChangedError: if the two differ by anything else.
    """
    source_ast, result_ast = _pandoc_ast_pair(source, result)
    before_canon, after_canon = _canonical(source_ast), _canonical(result_ast)
    if before_canon == after_canon:
        return []

    # Attribute the difference to the smallest set of normalizations that
    # reconciles it.  Merely containing a construct a normalization rewrites
    # (a bold heading or tight list that formatting *preserved*) must not
    # count as that normalization having been applied.
    for size in range(1, len(_NORMALIZATIONS) + 1):
        for combo in combinations(_NORMALIZATIONS, size):
            normalized_before, normalized_after = before_canon, after_canon
            for _key, _text, normalize in combo:
                normalized_before, normalized_after = normalize(
                    normalized_before, normalized_after
                )
            if normalized_before == normalized_after:
                return [key for key, _text, _normalize in combo]

    # Locate the difference against *every* normalization applied, not against the
    # raw trees. A block that a declared opinion reconciles is not the problem, and
    # naming it sends the reader to a block that is fine -- which costs exactly the
    # bisection this diagnostic exists to prevent. Whatever still differs when the
    # gate is at its most permissive is what actually blocked acceptance.
    permissive_before, permissive_after = before_canon, after_canon
    for _key, _text, normalize in _NORMALIZATIONS:
        permissive_before, permissive_after = normalize(
            permissive_before, permissive_after
        )

    # Canonicalizing or normalizing a block list always yields a block list.
    # The normalizations rewrite `before` only inside blocks, so its block indices
    # are still the source document's.
    block, detail = _first_difference(
        cast("list[PandocJson]", permissive_before),
        cast("list[PandocJson]", permissive_after),
    )
    raise MeaningChangedError(
        f"Refusing to write {label}: reformatting would change what pandoc reads "
        f"({detail}). The file is unchanged. "
        f"This is a flowmark bug -- please report it with the input document. To skip this "
        f"check and format anyway, pass --no-verify.",
        detail=detail,
        block=block,
    )

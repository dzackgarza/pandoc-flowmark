import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from strif import atomic_output_file

from flowmark.formats.frontmatter import split_frontmatter
from flowmark.formats.options import FormatOptions, ListSpacing, Pass, Plain, Semantic
from flowmark.linewrapping.markdown_filling import format_markdown
from flowmark.linewrapping.text_filling import Wrap, fill_text
from flowmark.linewrapping.text_wrapping import get_html_md_word_splitter
from flowmark.pandoc_reader import (
    located_source_nodes,
    PandocParseError,
    pandoc_executable,
)
from flowmark.pandoc_verify import (
    ALERT_TYPE,
    HYPHEN_JOIN,
    LAZY_LIST,
    LIST_SPACING,
    SMART_QUOTES,
    UNBOLD_HEADING,
    MeaningChangedError,
    block_indices,
    check_meaning_preserved,
    describe,
)
from flowmark.preflight import (
    MalformedInputError,
    preflight,
    rejected_math,
    unclosed_fences,
)


def _without_raw_blocks(body: str) -> str:
    """
    `body` with the lines of Pandoc's raw and code blocks emptied, so a `$` in raw
    TeX or code is not checked as Markdown math. Line numbers are kept.
    """
    lines = body.split("\n")
    for node in located_source_nodes(body, pandoc_executable()):
        if node.node.get("t") not in {"RawBlock", "CodeBlock"}:
            continue
        end = node.source_range.end
        last = end.line - 1 if end.column == 1 else end.line
        for index in range(node.source_range.start.line - 1, min(last, len(lines))):
            lines[index] = ""
    return "\n".join(lines)


REFORMAT_DEFAULTS = FormatOptions(Semantic(), frozenset({Pass.cleanups}))
"""Semantic line breaks and the cleanups: the options for a document."""

type Formatter = Callable[[str, FormatOptions, str], str]
"""Format a text with options; the last argument names the text in errors."""


def reformat_text_unchecked(text: str, options: FormatOptions = REFORMAT_DEFAULTS, label: str = "input") -> str:
    """
    Reformat text or Markdown and wrap lines, without checking that Pandoc reads
    the result as it reads `text`. A convenient wrapper around `fill_text()` and
    `format_markdown()`. `label` names the text in errors.

    Raises:
        MalformedInputError: in Markdown mode, if the document has `$...$` meant as
            math that pandoc reads as text (`rejected_math`), or a fence that is
            never closed (`unclosed_fences`).
    """
    if isinstance(options.wrap, Plain):
        return fill_text(
            text,
            text_wrap=Wrap.WRAP,
            width=options.wrap.width,
            word_splitter=get_html_md_word_splitter(),
        )
    # Math pandoc reads as text, or a fence never closed, is an error in the
    # document: formatting it would treat the author's TeX or code as prose. YAML
    # frontmatter is metadata, not Markdown, so only the body is checked; line
    # numbers count from the top of the file.
    frontmatter, body = split_frontmatter(text)
    offset = frontmatter.count("\n")
    rejected = sorted(
        [*rejected_math(_without_raw_blocks(body)), *unclosed_fences(body)],
        key=lambda f: f.line,
    )
    if rejected:
        named = "; ".join(f"{label}:{f.line + offset}: {f.message}" for f in rejected)
        raise MalformedInputError(f"Refusing to write {label}: {named}. The file is unchanged.")
    return format_markdown(text, options)


def reformat_text(text: str, options: FormatOptions = REFORMAT_DEFAULTS, label: str = "input") -> str:
    """
    `reformat_text_unchecked`, and in Markdown mode check with pandoc that the
    result parses to the same AST as `text`. Raise `MeaningChangedError` rather
    than return a document whose meaning changed. Requires the pandoc binary on
    PATH.
    """
    result = reformat_text_unchecked(text, options, label)
    if isinstance(options.wrap, Plain) or result == text:
        # An unchanged document trivially preserves meaning, so only a real
        # change pays for the pandoc comparison (reformatting an
        # already-formatted document is the common case).
        return result
    # Anything but flowmark's intentional normalizations raises here, so the
    # caller never gets a document whose meaning changed.
    try:
        applied = check_meaning_preserved(text, result, label)
    except MeaningChangedError as changed:
        # The gate can only ask "did flowmark break this?". Before answering
        # yes, ask the other question -- "was this already broken?" -- of the
        # block that changed. A suspect construct anywhere else in the
        # document is not the cause, and naming it sends the reader to a
        # line that is fine (#38).
        findings = preflight(text)
        if findings and changed.block is not None:
            blocks = block_indices(text, [f.line for f in findings])
            findings = [finding for finding, block in zip(findings, blocks, strict=True) if block == changed.block]
        if not findings:
            raise
        named = "; ".join(f"{label}:{finding.line}: {finding.message}" for finding in findings[:3])
        more = "" if len(findings) <= 3 else f" (and {len(findings) - 3} more)"
        raise MeaningChangedError(
            f"Refusing to write {label}: reformatting would change what "
            f"pandoc reads ({changed.detail}). The file is unchanged. Your input "
            f"looks ambiguous, so this is probably not a flowmark defect -- "
            f"{named}{more}. Fix the input, or pass --no-verify to format anyway.",
            detail=changed.detail,
            block=changed.block,
        ) from changed
    # Asking for a normalization and getting it is not news; getting one
    # without asking is, so only the latter is reported.
    requested = {
        UNBOLD_HEADING: Pass.cleanups in options.passes,
        LIST_SPACING: options.list_spacing is not ListSpacing.preserve,
        SMART_QUOTES: Pass.smartquotes in options.passes,
        HYPHEN_JOIN: Pass.cleanups in options.passes,
        ALERT_TYPE: True,
        # No flag asks for this one, so it is always worth saying: the
        # author's bullets under a paragraph line became a real list.
        LAZY_LIST: False,
    }
    for normalization in applied:
        if not requested[normalization]:
            print(
                f"Warning: {label}: {describe(normalization)} without being asked to",
                file=sys.stderr,
            )
    return result


@dataclass(frozen=True)
class Stdout:
    """Write the result to standard output."""


@dataclass(frozen=True)
class ToFile:
    """Write the result to `path`, making its parent directories."""

    path: Path


@dataclass(frozen=True)
class InPlace:
    """
    Write the result back to the input file, atomically, after saving the original
    with `backup_suffix` appended to its name; an empty suffix saves no backup.
    """

    backup_suffix: str = ".orig"


type Destination = Stdout | ToFile | InPlace


def reformat_file(
    path: Path | str,
    destination: Destination,
    options: FormatOptions,
    formatter: Formatter = reformat_text,
) -> None:
    """
    Reformat the text or Markdown file at `path`, or standard input for "-", with
    `formatter` and write the result to `destination`. Throws the usual
    file-related exceptions if the input or output is invalid.
    """
    read_stdin = path == "-"
    if isinstance(destination, InPlace) and read_stdin:
        raise ValueError("Cannot use `inplace` with stdin")
    text = sys.stdin.read() if read_stdin else Path(path).read_text()
    result = formatter(text, options, str(path))
    match destination:
        case Stdout():
            sys.stdout.write(result)
        case ToFile(output):
            with atomic_output_file(output, make_parents=True) as tmp_path:
                tmp_path.write_text(result)
        case InPlace(backup_suffix):
            with atomic_output_file(path, backup_suffix=backup_suffix, make_parents=True) as tmp_path:
                tmp_path.write_text(result)


def reformat_files(
    files: list[str],
    destination: Destination,
    options: FormatOptions,
    formatter: Formatter = reformat_text,
) -> int:
    """
    Reformat multiple files with the same options, and return how many were left
    unformatted because they were refused. Files are listed as for
    `reformat_file`; a `ToFile` destination takes one file only.
    """
    # Stdin, or one file with an output path: a single document with a single
    # destination, so a refusal raises instead of being reported as a batch skip.
    if len(files) == 1 and (files[0] == "-" or isinstance(destination, ToFile)):
        reformat_file(files[0], destination, options, formatter)
        return 0
    if isinstance(destination, ToFile):
        raise ValueError("Cannot specify output file when processing multiple files (use --inplace instead)")

    refused = 0
    for file_path in files:
        try:
            reformat_file(file_path, destination, options, formatter)
        except (MeaningChangedError, MalformedInputError) as e:
            # The document was left byte-identical; a per-file refusal must not
            # abort the batch.
            print(f"Warning: {e}", file=sys.stderr)
            refused += 1
        except PandocParseError as e:
            # Pandoc cannot read the document at all, so there is nothing to
            # format; it is an error in the input, left byte-identical.
            print(
                f"Warning: Refusing to write {file_path}: pandoc cannot parse it: {e}",
                file=sys.stderr,
            )
            refused += 1
    if refused:
        print(
            f"Warning: {refused} file{'s' if refused != 1 else ''} left unformatted "
            "(see warnings above: each names an error in the input, or a change to "
            "the pandoc-parsed meaning that is a flowmark bug worth reporting).",
            file=sys.stderr,
        )
    return refused

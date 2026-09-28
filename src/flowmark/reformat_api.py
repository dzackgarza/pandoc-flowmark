import sys
from pathlib import Path

from strif import atomic_output_file

from flowmark.formats.options import ListSpacing
from flowmark.linewrapping.markdown_filling import fill_markdown
from flowmark.linewrapping.text_filling import Wrap, fill_text
from flowmark.linewrapping.text_wrapping import get_html_md_word_splitter
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


def reformat_text(
    text: str,
    width: int = 88,
    plaintext: bool = False,
    semantic: bool = True,
    cleanups: bool = True,
    smartquotes: bool = False,
    ellipses: bool = False,
    list_spacing: ListSpacing = ListSpacing.loose,
    verify: bool = True,
    verify_label: str = "input",
) -> str:
    """
    Reformat text or markdown and wrap lines. Simply a convenient wrapper
    around `fill_text()` and `fill_markdown()` with reasonable defaults.

    Args:
        verify: Check with pandoc that the result parses to the same AST as the
            input, and raise `MeaningChangedError` if not, rather than return a
            document whose meaning changed. On by default; requires the pandoc
            binary on PATH. Markdown mode only.
        verify_label: How to name the document in a verification error.

    Raises:
        MalformedInputError: in Markdown mode, if the document has `$...$` meant as
            math that pandoc reads as text (`rejected_math`), or a fence that is
            never closed (`unclosed_fences`).
    """
    if plaintext:
        # Plaintext mode
        result = fill_text(
            text,
            text_wrap=Wrap.WRAP,
            width=width,
            word_splitter=get_html_md_word_splitter(),
        )
    else:
        # Markdown mode. Math pandoc reads as text, or a fence never closed, is an
        # error in the document: formatting it would treat the author's TeX or
        # code as prose.
        rejected = sorted(
            [*rejected_math(text), *unclosed_fences(text)], key=lambda f: f.line
        )
        if rejected:
            named = "; ".join(f"{verify_label}:{f.line}: {f.message}" for f in rejected)
            raise MalformedInputError(
                f"Refusing to write {verify_label}: {named}. The file is unchanged."
            )
        result = fill_markdown(
            text,
            # A document is not a docstring: its common indentation is content.
            # Dedenting one whose every line is indented turns a code block into
            # a paragraph.
            dedent_input=False,
            width=width,
            semantic=semantic,
            cleanups=cleanups,
            smartquotes=smartquotes,
            ellipses=ellipses,
            list_spacing=list_spacing,
            verify=False,
        )
        if verify and result != text:
            # An unchanged document trivially preserves meaning, so only a real
            # change pays for the pandoc comparison (reformatting an
            # already-formatted document is the common case).
            #
            # Anything but flowmark's intentional normalizations raises here, so the
            # caller never gets a document whose meaning changed.
            try:
                applied = check_meaning_preserved(text, result, verify_label)
            except MeaningChangedError as changed:
                # The gate can only ask "did flowmark break this?". Before answering
                # yes, ask the other question -- "was this already broken?" -- of the
                # block that changed. A suspect construct anywhere else in the
                # document is not the cause, and naming it sends the reader to a
                # line that is fine (#38).
                findings = preflight(text)
                if findings and changed.block is not None:
                    blocks = block_indices(text, [f.line for f in findings])
                    findings = [
                        finding
                        for finding, block in zip(findings, blocks, strict=True)
                        if block == changed.block
                    ]
                if not findings:
                    raise
                named = "; ".join(
                    f"{verify_label}:{finding.line}: {finding.message}"
                    for finding in findings[:3]
                )
                more = "" if len(findings) <= 3 else f" (and {len(findings) - 3} more)"
                raise MeaningChangedError(
                    f"Refusing to write {verify_label}: reformatting would change what "
                    f"pandoc reads ({changed.detail}). The file is unchanged. Your input "
                    f"looks ambiguous, so this is probably not a flowmark defect -- "
                    f"{named}{more}. Fix the input, or pass --no-verify to format anyway.",
                    detail=changed.detail,
                    block=changed.block,
                ) from changed
            # Asking for a normalization and getting it is not news; getting one
            # without asking is, so only the latter is reported.
            requested = {
                UNBOLD_HEADING: cleanups,
                LIST_SPACING: list_spacing is not ListSpacing.preserve,
                SMART_QUOTES: smartquotes,
                HYPHEN_JOIN: cleanups,
                ALERT_TYPE: True,
                # No flag asks for this one, so it is always worth saying: the
                # author's bullets under a paragraph line became a real list.
                LAZY_LIST: False,
            }
            for normalization in applied:
                if not requested.get(normalization, False):
                    print(
                        f"Warning: {verify_label}: {describe(normalization)} without being asked to",
                        file=sys.stderr,
                    )

    return result


def reformat_file(
    path: Path | str,
    output: Path | str | None,
    width: int = 88,
    inplace: bool = False,
    nobackup: bool = False,
    plaintext: bool = False,
    semantic: bool = False,
    cleanups: bool = True,
    smartquotes: bool = False,
    ellipses: bool = False,
    make_parents: bool = True,
    list_spacing: ListSpacing = ListSpacing.loose,
    verify: bool = True,
) -> None:
    """
    Reformat text or markdown and wrap lines on the given files.
    Accepts "-" for stdin. Can omit output if `inplace` is True.
    Throws usual file-related exceptions if the input or output is invalid.

    Args:
        path: Path to the input file, or "-" for stdin.
        output: Path to the output file, or "-" for stdout.
        width: The width to wrap lines to.
        inplace: Whether to write the file back to the same path (atomically only on success).
        nobackup: Whether to not make a backup of the original file
        plaintext: Use plaintext instead of Markdown mode wrapping.
        semantic: Use semantic line breaks (based on sentences) heuristic.
        cleanups: Enable (safe) cleanups for common issues like accidentally boldfaced section
            headers (only applies to Markdown mode).
        smartquotes: Convert straight quotes to typographic (curly) quotes and apostrophes
            (only applies to Markdown mode).
        ellipses: Convert three dots (...) to ellipsis character (…) with normalized spacing
            (only applies to Markdown mode).
        make_parents: Whether to make parent directories if they don't exist.
        list_spacing: Control list spacing: "loose" (default), "preserve", or "tight".
        verify: Check with pandoc that reformatting did not change the document's
            parsed AST, and write nothing if it did. On by default (only applies
            to Markdown mode).
    """
    read_stdin = path == "-"
    write_stdout = output == "-" or not output

    if inplace and read_stdin:
        raise ValueError("Cannot use `inplace` with stdin")

    if read_stdin:
        text = sys.stdin.read()
    else:
        text = Path(path).read_text()

    result = reformat_text(
        text,
        width,
        plaintext,
        semantic,
        cleanups,
        smartquotes,
        ellipses,
        list_spacing,
        verify=verify,
        verify_label=str(path),
    )

    if inplace:
        backup_suffix = ".orig" if not nobackup else ""
        with atomic_output_file(
            path, backup_suffix=backup_suffix, make_parents=make_parents
        ) as tmp_path:
            tmp_path.write_text(result)
    else:
        if not output or write_stdout:
            sys.stdout.write(result)
        else:
            with atomic_output_file(output, make_parents=make_parents) as tmp_path:
                tmp_path.write_text(result)


def reformat_files(
    files: list[str],
    output: str | None = None,
    width: int = 88,
    inplace: bool = False,
    nobackup: bool = False,
    plaintext: bool = False,
    semantic: bool = False,
    cleanups: bool = True,
    smartquotes: bool = False,
    ellipses: bool = False,
    make_parents: bool = True,
    list_spacing: ListSpacing = ListSpacing.loose,
    verify: bool = True,
) -> int:
    """
    Reformat multiple files with the same options, and return how many were left
    unformatted because they were refused.

    Args:
        files: List of file paths to process, or ["-"] for stdin.
        output: Output file path (ignored when inplace=True, use "-" for stdout).
        width: The width to wrap lines to.
        inplace: Whether to write files back to their original paths.
        nobackup: Whether to not make backups of original files.
        plaintext: Use plaintext instead of Markdown mode wrapping.
        semantic: Use semantic line breaks (based on sentences) heuristic.
        cleanups: Enable (safe) cleanups for common issues.
        smartquotes: Convert straight quotes to typographic quotes.
        ellipses: Convert three dots to ellipsis character.
        make_parents: Whether to make parent directories if they don't exist.
        list_spacing: Control list spacing: "loose" (default), "preserve", or "tight".
        verify: Check with pandoc that reformatting did not change any document's
            parsed AST, and write nothing if it did. On by default (only applies
            to Markdown mode).
    """
    # Stdin, or one file with an output path: a single document with a single
    # destination, so a refusal raises instead of being reported as a batch skip.
    if len(files) == 1 and (
        files[0] == "-" or (output and output != "-" and not inplace)
    ):
        reformat_file(
            path=files[0],
            output=output,
            width=width,
            inplace=inplace,
            nobackup=nobackup,
            plaintext=plaintext,
            semantic=semantic,
            cleanups=cleanups,
            smartquotes=smartquotes,
            ellipses=ellipses,
            make_parents=make_parents,
            list_spacing=list_spacing,
            verify=verify,
        )
        return 0

    # Multiple files case
    if not inplace and output and output != "-":
        raise ValueError(
            "Cannot specify output file when processing multiple files (use --inplace instead)"
        )

    refused = 0
    for file_path in files:
        if inplace:
            # Process each file in-place
            output = None
        else:
            # Process each file to stdout
            output = "-"
        try:
            reformat_file(
                path=file_path,
                output=output,
                width=width,
                inplace=inplace,
                nobackup=nobackup,
                plaintext=plaintext,
                semantic=semantic,
                cleanups=cleanups,
                smartquotes=smartquotes,
                ellipses=ellipses,
                make_parents=make_parents,
                list_spacing=list_spacing,
                verify=verify,
            )
        except (MeaningChangedError, MalformedInputError) as e:
            # The document was left byte-identical; a per-file refusal must not
            # abort the batch.
            print(f"Warning: {e}", file=sys.stderr)
            refused += 1
    if refused:
        print(
            f"Warning: {refused} file{'s' if refused != 1 else ''} left unformatted "
            "(see warnings above: each names an error in the input, or a change to "
            "the pandoc-parsed meaning that is a flowmark bug worth reporting).",
            file=sys.stderr,
        )
    return refused

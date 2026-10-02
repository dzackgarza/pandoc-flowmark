#!/usr/bin/env python3
"""
Flowmark: Better auto-formatting for Markdown and plaintext

By default, Flowmark puts each sentence on its own line (semantic line breaks).
Use --no-semantic to wrap paragraphs to a column width instead.

Common usage:
  flowmark README.md
  flowmark --auto README.md
  flowmark --auto docs/
  flowmark --auto .
  flowmark --list-files .

Settings:
  An explicit flag overrides the config file (.flowmark.toml, flowmark.toml, or
  [tool.flowmark] in pyproject.toml, in this directory or a parent). The config
  file overrides the --auto preset, and --auto overrides the built-in defaults.

Agent usage:
  flowmark --skill
  Agents should run `flowmark --skill` for full Flowmark usage guidance.

Use `flowmark --docs` for full documentation.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
from dataclasses import dataclass
from pathlib import Path

from flowmark.config import ConfigError, find_config_file, load_config
from flowmark.formats.options import (
    FormatOptions,
    ListSpacing,
    Pass,
    Plain,
    Semantic,
    Width,
    WrapMode,
)
from flowmark.linewrapping.line_wrappers import DEFAULT_MIN_LINE_LEN
from flowmark.linewrapping.text_filling import DEFAULT_WRAP_WIDTH
from flowmark.pandoc_reader import PandocUnavailableError
from flowmark.reformat_api import (
    Destination,
    InPlace,
    Stdout,
    ToFile,
    reformat_files,
    reformat_text,
    reformat_text_unchecked,
)

# The settings `--auto` turns on. They are defaults beneath the config file, so a
# config setting or an explicit flag overrides each of them.
_AUTO_PRESET = {
    "inplace": True,
    "nobackup": True,
    "semantic": True,
    "cleanups": True,
    "smartquotes": True,
    "ellipses": True,
}


@dataclass
class Options:
    """Command-line options for the flowmark tool, one field per argparse destination."""

    files: list[str]
    output: str
    width: int | None
    plaintext: bool
    semantic: bool
    cleanups: bool
    smartquotes: bool
    ellipses: bool
    verify: bool
    list_spacing: ListSpacing
    inplace: bool
    nobackup: bool
    auto: bool
    # File discovery options
    extend_include: list[str]
    exclude: list[str] | None
    extend_exclude: list[str]
    respect_gitignore: bool
    force_exclude: bool
    list_files: bool
    files_max_size: int
    version: bool
    # Agent skill options
    skill_instructions: bool
    install_skill: bool
    agent_base: str | None
    docs: bool

    @property
    def line_width(self) -> int:
        """
        The width to wrap to: `--width` if given, else none (0) for semantic line
        breaks, which apply to Markdown only, else the column width.
        """
        if self.width is not None:
            return self.width
        return 0 if self.semantic and not self.plaintext else DEFAULT_WRAP_WIDTH

    @property
    def format_options(self) -> FormatOptions:
        """The formatting the flags select."""
        passes = {
            Pass.cleanups: self.cleanups,
            Pass.smartquotes: self.smartquotes,
            Pass.ellipses: self.ellipses,
        }
        wrap: WrapMode
        if self.plaintext:
            wrap = Plain(self.line_width)
        elif self.semantic:
            wrap = Semantic(self.line_width)
        else:
            wrap = Width(self.line_width)
        return FormatOptions(
            wrap,
            frozenset(name for name, enabled in passes.items() if enabled),
            self.list_spacing,
        )

    @property
    def destination(self) -> Destination:
        """Where the flags send the result."""
        if self.inplace:
            return InPlace("" if self.nobackup else ".orig")
        if self.output and self.output != "-":
            return ToFile(Path(self.output))
        return Stdout()


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, with the built-in defaults."""
    # The module docstring's first two paragraphs are the description; the rest is
    # the epilog.
    module_doc = __doc__ or ""
    doc_parts = module_doc.split("\n\n")
    description = "\n\n".join(doc_parts[:2])
    epilog = "\n\n".join(doc_parts[2:])

    parser = argparse.ArgumentParser(
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=str,
        default=[],
        help="Input files or directories (required; use '-' for stdin, '.' for current directory)",
    )
    # Each names where the result goes, so argparse refuses any two together.
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "-o",
        "--output",
        type=str,
        default="-",
        help="Output file (use '-' for stdout, the default). Only one input file may be "
        "given; not with --inplace or --auto",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=int,
        default=None,
        help="Line width to wrap to, or 0 to disable line wrapping. When not given: "
        "with semantic line breaks, no limit (each sentence gets its own line); with "
        f"--no-semantic or --plaintext, {DEFAULT_WRAP_WIDTH}. With semantic line breaks and "
        "-w N, sentences are split first, and a sentence longer than N is then "
        f"wrapped to N; a line shorter than {DEFAULT_MIN_LINE_LEN} characters is joined "
        "with the next sentence when both fit in N",
    )
    parser.add_argument(
        "-p",
        "--plaintext",
        action="store_true",
        help="Process as plaintext (no Markdown parsing)",
    )
    parser.add_argument(
        "-s",
        "--semantic",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Semantic line breaks: put each sentence on its own line (default: "
        "%(default)s). --no-semantic wraps paragraphs to a column width instead "
        "(only applies to Markdown mode)",
    )
    parser.add_argument(
        "-c",
        "--cleanups",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable (safe) cleanups for common issues like accidentally boldfaced section headers (default: %(default)s; only applies to Markdown mode)",
    )
    parser.add_argument(
        "--smartquotes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Convert straight quotes to typographic (curly) quotes and apostrophes (default: %(default)s; only applies to Markdown mode)",
    )
    parser.add_argument(
        "--ellipses",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Convert three dots (...) to ellipsis character (…) with normalized spacing (default: %(default)s; only applies to Markdown mode)",
    )
    parser.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Check with pandoc that the output parses to the same AST as the input, and "
        "fail without writing if it does not (default: %(default)s). This is a safety "
        "gate: it catches any bug where flowmark would change a document's meaning rather "
        "than just its spelling. Requires the `pandoc` binary on PATH. Pass --no-verify "
        "to skip the check and write anyway (only applies to Markdown mode)",
    )
    parser.add_argument(
        "--list-spacing",
        type=ListSpacing,
        choices=list(ListSpacing),
        default=ListSpacing.loose,
        help="List spacing: 'loose' puts a blank line between all items, 'tight' "
        "removes blank lines where possible, 'preserve' keeps each list as written "
        "(default: %(default)s). Flowmark normalizes list spacing to one style, as it "
        "normalizes other formatting",
    )
    destination.add_argument(
        "-i",
        "--inplace",
        action="store_true",
        help="Edit the file in place",
    )
    parser.add_argument(
        "--nobackup",
        action="store_true",
        help="Do not make a backup of the original file when using --inplace",
    )
    destination.add_argument(
        "--auto",
        action="store_true",
        help="Fully auto-format files in place: sets `--inplace --nobackup --semantic "
        "--cleanups --smartquotes --ellipses`. The config file and explicit flags "
        "override these (e.g. `--auto --no-smartquotes`). Requires at least one file "
        "or directory argument (use '.' for current directory)",
    )
    # File discovery options
    parser.add_argument(
        "--extend-include",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional file patterns to include (e.g., '*.mdx'). Can be repeated; adds to the config file's patterns",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="PATTERN",
        help="Replace all default exclusion patterns. Can be repeated; adds to the config file's patterns",
    )
    parser.add_argument(
        "--extend-exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Add to default exclusion patterns (e.g., 'drafts/'). Can be repeated; adds to the config file's patterns",
    )
    parser.add_argument(
        "--respect-gitignore",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip files that .gitignore ignores (default: %(default)s)",
    )
    parser.add_argument(
        "--force-exclude",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Apply exclusion patterns even to files named explicitly on the command line (default: %(default)s)",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        dest="list_files",
        help="Print resolved file paths without formatting. Requires at least one file or directory argument (use '.' for current directory)",
    )
    parser.add_argument(
        "--files-max-size",
        type=int,
        default=1_048_576,
        dest="files_max_size",
        metavar="BYTES",
        help="Skip files larger than this size in bytes (0 = no limit, default: %(default)s)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version information and exit",
    )
    # Agent skill options
    parser.add_argument(
        "--skill",
        action="store_true",
        dest="skill_instructions",
        help="Print skill instructions (SKILL.md content) for Claude Code",
    )
    parser.add_argument(
        "--install-skill",
        action="store_true",
        dest="install_skill",
        help="Install Claude Code skill for flowmark",
    )
    parser.add_argument(
        "--agent-base",
        type=str,
        dest="agent_base",
        metavar="DIR",
        help="Agent config directory for skill installation (default: ~/.claude)",
    )
    parser.add_argument(
        "--docs",
        action="store_true",
        help="Print full documentation",
    )
    return parser


def _needs_file_resolution(files: list[str]) -> bool:
    """Check if any input paths need file resolution (directories or globs)."""
    for f in files:
        if f == "-":
            continue
        if Path(f).is_dir():
            return True
        if any(c in f for c in "*?["):
            return True
    return False


def _resolve_files(options: Options) -> list[str]:
    """
    If inputs include directories or globs, use FileResolver to expand them.
    Otherwise, pass through unchanged for backward compatibility.
    """
    if not _needs_file_resolution(options.files) and not options.list_files:
        return options.files

    from flowmark.file_resolver import FileResolver, FileResolverConfig

    # Filter out stdin marker before passing to resolver
    resolvable = [f for f in options.files if f != "-"]
    stdin_present = len(resolvable) < len(options.files)

    config = FileResolverConfig(
        extend_include=options.extend_include,
        exclude=options.exclude,
        extend_exclude=options.extend_exclude,
        respect_gitignore=options.respect_gitignore,
        force_exclude=options.force_exclude,
        files_max_size=options.files_max_size,
    )
    resolver = FileResolver(config)
    resolved = resolver.resolve(resolvable)
    result = [str(p) for p in resolved]
    if stdin_present:
        result.insert(0, "-")
    return result


def main(args: list[str] | None = None) -> int:
    """
    Main entry point for the flowmark CLI.

    Args:
        args: Command-line arguments (uses sys.argv if None)

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    parser = _build_parser()
    options = Options(**vars(parser.parse_args(args)))

    # Display version information if requested
    if options.version:
        try:
            version = importlib.metadata.version("flowmark")
            print(f"v{version}")
        except importlib.metadata.PackageNotFoundError:
            print("unknown (package not installed)")
        return 0

    # Handle skill-related options (early exit)
    if options.install_skill:
        from flowmark.skill import install_skill

        install_skill(agent_base=options.agent_base)
        return 0

    if options.skill_instructions:
        from flowmark.skill import get_skill_content

        print(get_skill_content())
        return 0

    if options.docs:
        from flowmark.skill import get_docs_content

        print(get_docs_content())
        return 0

    # Require explicit file/directory arguments.
    # (Use '.' for the current directory, '-' for stdin.)
    if not options.files:
        if options.auto:
            print(
                "Error: --auto requires at least one file or directory argument (use '.' for current directory, --help for more options)",
                file=sys.stderr,
            )
            return 1
        if options.list_files:
            print(
                "Error: --list-files requires at least one file or directory argument (use '.' for current directory, --help for more options)",
                file=sys.stderr,
            )
            return 1
        print(
            "Error: No input specified. Provide files, directories (use '.' for current directory), or '-' for stdin. Use --help for more options.",
            file=sys.stderr,
        )
        return 1

    # Layer the --auto preset and then the config file over the built-in defaults,
    # and parse again: an explicit flag overrides every default.
    if options.auto:
        parser.set_defaults(**_AUTO_PRESET)
    try:
        config_path = find_config_file(Path.cwd())
        if config_path:
            parser.set_defaults(**load_config(config_path))
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    options = Options(**vars(parser.parse_args(args)))

    # Resolve files if any input is a directory, glob, or --list-files is used
    resolved_files = _resolve_files(options)

    # Handle --list-files mode (print and exit)
    if options.list_files:
        for f in resolved_files:
            print(f)
        return 0

    try:
        refused = reformat_files(
            resolved_files,
            options.destination,
            options.format_options,
            reformat_text if options.verify else reformat_text_unchecked,
        )
    except ValueError as e:
        # Handle errors reported by reformat_file, like using --inplace with stdin.
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (OSError, PandocUnavailableError) as e:
        # A file cannot be read or written, or pandoc cannot run.
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # A refused file was left unchanged and named on stderr; the run still failed
    # for it, and a commit hook shows that output only on a non-zero exit.
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())

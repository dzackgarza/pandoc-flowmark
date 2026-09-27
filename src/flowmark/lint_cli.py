"""Command-line interface for the standalone Flowmark linter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict

from flowmark.file_resolver import FileResolver, FileResolverConfig
from flowmark.formats.options import ListSpacing
from flowmark.lint import LintOptions, StyleRule, lint_text


class LintFileResult(TypedDict):
    """Stable per-file JSON payload."""

    path: str
    diagnostics: list[dict[str, object]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowmark-lint",
        description="Lint Pandoc-flavoured Markdown with Flowmark's semantic parser.",
    )
    parser.add_argument(
        "files", nargs="+", help="Markdown files/directories, or '-' for stdin"
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", dest="output_format"
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Return 0 even when diagnostics are present (editor integration)",
    )
    parser.add_argument(
        "--no-format-check",
        action="store_true",
        help="Run semantic ambiguity checks only",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=0,
        help="Canonical line width; 0 disables width wrapping (default: 0)",
    )
    parser.add_argument(
        "--semantic", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--cleanups", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--smartquotes", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--ellipses", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--list-spacing", choices=("preserve", "loose", "tight"), default="preserve"
    )
    parser.add_argument(
        "--style",
        action="append",
        choices=tuple(rule.value for rule in StyleRule),
        default=[],
        metavar="RULE",
        help="Enable an opt-in style rule; may be repeated",
    )
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=None,
        metavar="N",
        help="Warn when an ordinary prose line exceeds N characters",
    )
    parser.add_argument(
        "--source-path",
        type=str,
        default=None,
        metavar="PATH",
        help="Path context for stdin, used to resolve relative links",
    )
    return parser


def _resolve_files(arguments: list[str]) -> list[str]:
    stdin = [value for value in arguments if value == "-"]
    ordinary = [value for value in arguments if value != "-"]
    if not ordinary:
        return stdin
    needs_resolution = any(
        Path(value).is_dir() or any(char in value for char in "*?[")
        for value in ordinary
    )
    resolved = (
        [
            str(path)
            for path in FileResolver(FileResolverConfig(tool_name="flowmark")).resolve(
                ordinary
            )
        ]
        if needs_resolution
        else ordinary
    )
    return stdin + resolved


def _options(args: argparse.Namespace) -> LintOptions:
    return LintOptions(
        width=args.width,
        semantic=args.semantic,
        cleanups=args.cleanups,
        smartquotes=args.smartquotes,
        ellipses=args.ellipses,
        list_spacing=ListSpacing(args.list_spacing),
        check_format=not args.no_format_check,
        styles=frozenset(StyleRule(value) for value in args.style),
        max_line_length=args.max_line_length,
    )


def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text()


def _text_line(path: str, diagnostic: dict[str, object]) -> str:
    return (
        f"{path}:{diagnostic['line']}:{diagnostic['column']}: "
        f"{diagnostic['severity']} {diagnostic['rule']}: {diagnostic['message']}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    options = _options(args)
    paths = _resolve_files(args.files)
    results: list[LintFileResult] = []
    diagnostic_count = 0

    for path in paths:
        source_path = (
            Path(args.source_path)
            if path == "-" and args.source_path is not None
            else (None if path == "-" else Path(path))
        )
        diagnostics = [
            diagnostic.to_json()
            for diagnostic in lint_text(_read(path), options, source_path=source_path)
        ]
        diagnostic_count += len(diagnostics)
        results.append({"path": path, "diagnostics": diagnostics})

    if args.output_format == "json":
        json.dump({"version": 1, "files": results}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        for result in results:
            for diagnostic in result["diagnostics"]:
                print(_text_line(str(result["path"]), diagnostic))

    if args.exit_zero:
        return 0
    return 1 if diagnostic_count else 0


if __name__ == "__main__":
    raise SystemExit(main())

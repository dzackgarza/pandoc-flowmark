"""Command-line interface for the standalone Flowmark linter."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict, cast

from flowmark.config import ConfigError, LintConfig, find_config_file, load_lint_config
from flowmark.file_resolver import FileResolver, FileResolverConfig
from flowmark.lint import LintOptions, RuleLevel, StyleRule, lint_rules, lint_text
from flowmark.lint_engine import LintRule


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
        "files", nargs="*", help="Markdown files/directories, or '-' for stdin"
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
    parser.add_argument(
        "--rule",
        action="append",
        default=[],
        metavar="RULE=LEVEL",
        help="Override a named rule with off, info, warning, or error; may be repeated",
    )
    parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        metavar="MODULE_OR_FILE",
        help="Load a lint rule extension module/file; may be repeated",
    )
    parser.add_argument(
        "--no-discover-plugins",
        action="store_true",
        help="Do not discover installed flowmark.lint_rules entry points",
    )
    parser.add_argument(
        "--context",
        type=str,
        default=None,
        metavar="JSON_FILE",
        help="JSON object exposed to lint extensions as rule context",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="TOML_FILE",
        help="Use this Flowmark config instead of searching parent directories",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Disable Flowmark config-file discovery",
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="List the effective named lint rules and exit",
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


def _rule_overrides(
    values: list[str], parser: argparse.ArgumentParser
) -> dict[str, str]:
    overrides: dict[str, str] = {}
    allowed = {level.value for level in RuleLevel}
    for value in values:
        if "=" not in value:
            parser.error(f"--rule requires RULE=LEVEL, got {value!r}")
        rule, level = value.rsplit("=", 1)
        rule = rule.strip()
        level = level.strip()
        if not rule or level not in allowed:
            parser.error(
                f"--rule requires a rule name and one of {', '.join(sorted(allowed))}"
            )
        overrides[rule] = level
    return overrides


def _context(path: str | None, parser: argparse.ArgumentParser) -> dict[str, object]:
    if path is None:
        return {}
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"Could not read lint context {path!r}: {error}")
    if not isinstance(value, dict):
        parser.error("Lint context JSON must contain an object")
    typed = cast(dict[object, object], value)
    return {str(key): item for key, item in typed.items()}


def _options(
    args: argparse.Namespace,
    config: LintConfig,
    parser: argparse.ArgumentParser,
) -> LintOptions:
    rules = dict(config.rules)
    rules.update(_rule_overrides(args.rule, parser))
    context = dict(config.context)
    context.update(_context(args.context, parser))
    configured_plugins = config.plugins
    cli_plugins = tuple(args.plugin)
    max_line_length = (
        args.max_line_length
        if args.max_line_length is not None
        else config.max_line_length
    )
    discover_plugins = (
        False
        if args.no_discover_plugins
        else (True if config.discover_plugins is None else config.discover_plugins)
    )
    return LintOptions(
        styles=frozenset(StyleRule(value) for value in args.style),
        max_line_length=max_line_length,
        rules=rules,
        plugins=configured_plugins + cli_plugins,
        context=context,
        discover_plugins=discover_plugins,
    )


def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text()


def _text_line(path: str, diagnostic: dict[str, object]) -> str:
    line = (
        f"{path}:{diagnostic['line']}:{diagnostic['column']}: "
        f"{diagnostic['severity']} {diagnostic['rule']}: {diagnostic['message']}"
    )
    # One indented `help:` line per suggested fix, as rustc prints them.
    suggestions = cast(list[dict[str, str]], diagnostic.get("suggestions", []))
    return "".join([line, *(f"\n  help: {item['title']}" for item in suggestions)])


def _load(config_path: Path, parser: argparse.ArgumentParser) -> LintConfig:
    try:
        return load_lint_config(config_path)
    except ConfigError as error:
        parser.error(str(error))


def _config_for(
    path: str | None,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> LintConfig:
    if args.no_config:
        return LintConfig()
    if args.config is not None:
        config_path = Path(args.config)
        if not config_path.is_file():
            parser.error(f"Flowmark config does not exist: {args.config}")
        return _load(config_path, parser)

    source_path = cast(str | None, args.source_path)
    if path == "-" and source_path is not None:
        start = Path(source_path).expanduser().resolve().parent
    elif path not in {None, "-"}:
        assert path is not None
        candidate = Path(path).expanduser()
        start = (
            candidate.resolve().parent if candidate.is_file() else candidate.resolve()
        )
    else:
        start = Path.cwd()
    config_path = find_config_file(start)
    return LintConfig() if config_path is None else _load(config_path, parser)


def _rule_payload(rule: LintRule) -> dict[str, object]:
    return {
        "name": rule.name,
        "default_level": rule.default_level.value,
        "description": rule.description,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        config = _config_for(None, args, parser)
        options = _options(args, config, parser)
        rules = [_rule_payload(rule) for rule in lint_rules(options)]
        if args.output_format == "json":
            json.dump({"version": 1, "rules": rules}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            for rule in rules:
                print(f"{rule['name']}\t{rule['default_level']}\t{rule['description']}")
        return 0

    if not args.files:
        parser.error("at least one Markdown file/directory or '-' is required")

    paths = _resolve_files(args.files)
    results: list[LintFileResult] = []
    diagnostic_count = 0

    for path in paths:
        config = _config_for(path, args, parser)
        options = _options(args, config, parser)
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

"""
TOML config file loading for Flowmark.

Searches for `.flowmark.toml`, `flowmark.toml`, or `pyproject.toml [tool.flowmark]`
in the current directory and then in each parent. A config key is the long name of a
CLI flag (`list-spacing` for `--list-spacing`). The CLI applies the config as argparse
defaults, so an explicit flag always overrides it.

The `[lint]` table configures `flowmark-lint` instead; `load_lint_config` reads it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from flowmark.formats.options import ListSpacing

ConfigValue = int | bool | str | list[str]

type TomlTable = dict[str, TomlTable | ConfigValue]

# The keys a config file may set, by argparse destination, with the TOML type each
# must hold.
CONFIG_KEYS: dict[str, type[ConfigValue]] = {
    # Formatting
    "width": int,
    "semantic": bool,
    "cleanups": bool,
    "smartquotes": bool,
    "ellipses": bool,
    "list_spacing": str,
    # File discovery
    "extend_include": list,
    "exclude": list,
    "extend_exclude": list,
    "files_max_size": int,
    "respect_gitignore": bool,
    "force_exclude": bool,
}

# Tables that only group keys: their keys are read as top-level keys.
_SECTIONS = ("formatting", "file-discovery")

# The table read by flowmark-lint rather than the formatter.
_LINT_TABLE = "lint"

# Config file search order (first match wins within each directory level)
_CONFIG_FILENAMES = [".flowmark.toml", "flowmark.toml", "pyproject.toml"]


class ConfigError(ValueError):
    """A config file that cannot be read as Flowmark settings."""


def find_config_file(start_dir: Path) -> Path | None:
    """
    Return the first config file in `start_dir` or a parent directory, or `None`.
    Search order per directory: `.flowmark.toml` > `flowmark.toml` >
    `pyproject.toml` (only if it has `[tool.flowmark]`).
    """
    start = start_dir.resolve()
    for directory in [start, *start.parents]:
        for filename in _CONFIG_FILENAMES:
            candidate = directory / filename
            if candidate.is_file() and _flowmark_table(candidate) is not None:
                return candidate
    return None


def load_config(config_path: Path) -> dict[str, ConfigValue]:
    """
    Read the settings in a config file, keyed by argparse destination. Raises
    `ConfigError`, naming the file, for unparsable TOML, an unknown key, or a
    value of the wrong type.
    """
    table = _flowmark_table(config_path)
    if table is None:
        raise ConfigError(f"{config_path}: no [tool.flowmark] table")

    flat: TomlTable = {}
    for key, value in table.items():
        if key == _LINT_TABLE:
            continue
        if key in _SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value

    config: dict[str, ConfigValue] = {}
    for key, value in flat.items():
        dest = key.replace("-", "_")
        expected = CONFIG_KEYS.get(dest)
        if expected is None:
            raise ConfigError(f"{config_path}: unknown config key '{key}'")
        if isinstance(value, dict) or type(value) is not expected:
            raise ConfigError(
                f"{config_path}: config key '{key}' must be {expected.__name__}, "
                f"not {type(value).__name__}"
            )
        if dest == "list_spacing" and value not in ListSpacing:
            raise ConfigError(
                f"{config_path}: config key '{key}' must be one of "
                f"{', '.join(ListSpacing)}, not '{value}'"
            )
        config[dest] = value
    return config


def _empty_table() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class LintConfig:
    """The `[lint]` table of a config file."""

    rules: dict[str, object] = field(default_factory=_empty_table)
    plugins: tuple[str, ...] = ()
    context: dict[str, object] = field(default_factory=_empty_table)
    max_line_length: int | None = None
    discover_plugins: bool | None = None


def load_lint_config(config_path: Path) -> LintConfig:
    """
    Read the `[lint]` table of a config file. Raises `ConfigError`, naming the
    file, for an unknown key or a value of the wrong type.
    """
    table = _flowmark_table(config_path)
    if table is None:
        raise ConfigError(f"{config_path}: no [tool.flowmark] table")
    lint = table.get(_LINT_TABLE, {})
    if not isinstance(lint, dict):
        raise ConfigError(f"{config_path}: [lint] must be a table")
    lint = {key.replace("_", "-"): value for key, value in lint.items()}

    def typed[T](key: str, expected: type[T]) -> T | None:
        value = lint.get(key)
        if value is not None and not isinstance(value, expected):
            raise ConfigError(
                f"{config_path}: lint key '{key}' must be {expected.__name__}, "
                f"not {type(value).__name__}"
            )
        return value

    unknown = set(lint) - {
        "rules",
        "plugins",
        "context",
        "max-line-length",
        "discover-plugins",
    }
    if unknown:
        raise ConfigError(f"{config_path}: unknown lint key '{sorted(unknown)[0]}'")
    plugins = cast(list[object], typed("plugins", list) or [])
    if not all(isinstance(item, str) for item in plugins):
        raise ConfigError(
            f"{config_path}: lint key 'plugins' must be a list of strings"
        )
    return LintConfig(
        rules=cast(dict[str, object], typed("rules", dict) or {}),
        plugins=tuple(cast(list[str], plugins)),
        context=cast(dict[str, object], typed("context", dict) or {}),
        max_line_length=typed("max-line-length", int),
        discover_plugins=typed("discover-plugins", bool),
    )


def _flowmark_table(path: Path) -> TomlTable | None:
    """The Flowmark settings in `path`, or `None` for a pyproject.toml without them."""
    try:
        table: TomlTable = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    if path.name != "pyproject.toml":
        return table
    tool = table.get("tool")
    flowmark = tool.get("flowmark") if isinstance(tool, dict) else None
    return flowmark if isinstance(flowmark, dict) else None

"""Named-rule registry and extension API for Flowmark linting.

The linter is a standalone engine. Editors, CI jobs, pre-commit hooks, and other
clients all configure and invoke the same rule registry. GUI integrations may
provide data to rules, but they do not own or execute lint semantics themselves.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from flowmark.pandoc_lint import PandocJson


class RuleLevel(StrEnum):
    """Configured state/severity of a named lint rule."""

    OFF = "off"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def _empty_mapping() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class Suggestion:
    """One candidate fix: replace the finding's whole source range with ``replacement``.

    ``title`` is the short imperative an editor shows on its fix button, e.g.
    "Use `\\sin`". Modeled on ESLint suggestions (``desc`` + fix) and rustc's
    ``help`` suggestions.
    """

    title: str
    replacement: str


@dataclass(frozen=True)
class RuleFinding:
    """One lint finding at a half-open source range."""

    rule: str
    severity: str
    message: str
    start: int
    end: int
    suggestions: tuple[Suggestion, ...] = ()
    data: Mapping[str, object] = field(default_factory=_empty_mapping)


@dataclass(frozen=True)
class RuleSetting:
    """Effective per-rule configuration."""

    level: RuleLevel | None = None
    options: Mapping[str, object] = field(default_factory=_empty_mapping)


@dataclass
class RuleContext:
    """Shared immutable source facts plus extension-supplied context."""

    text: str
    pandoc_document: dict[str, PandocJson]
    source_path: Path | None
    data: Mapping[str, object] = field(default_factory=_empty_mapping)
    cache: dict[str, object] = field(default_factory=_empty_mapping)


class RuleCheck(Protocol):
    def __call__(
        self,
        context: RuleContext,
        options: Mapping[str, object],
        /,
    ) -> Iterable[RuleFinding]: ...


@dataclass(frozen=True)
class LintRule:
    """One named rule in the registry."""

    name: str
    description: str
    default_level: RuleLevel = RuleLevel.WARNING
    check: RuleCheck | None = None


class RuleRegistry:
    """Registry of built-in and extension-provided named lint rules."""

    def __init__(self) -> None:
        self._rules: dict[str, LintRule] = {}

    def register(self, rule: LintRule, *, replace: bool = False) -> None:
        if not rule.name or any(char.isspace() for char in rule.name):
            raise ValueError(f"Invalid lint rule name: {rule.name!r}")
        if rule.name in self._rules and not replace:
            raise ValueError(f"Lint rule {rule.name!r} is already registered")
        self._rules[rule.name] = rule

    def register_many(self, rules: Iterable[LintRule]) -> None:
        for rule in rules:
            self.register(rule)

    def get(self, name: str) -> LintRule | None:
        return self._rules.get(name)

    def rules(self) -> tuple[LintRule, ...]:
        return tuple(self._rules[name] for name in sorted(self._rules))

    def rule_names(self) -> frozenset[str]:
        return frozenset(self._rules)


LintPlugin = Callable[[RuleRegistry], None]


def _register_plugin_object(registry: RuleRegistry, plugin: object, label: str) -> None:
    if callable(plugin):
        _ = plugin(registry)
        return
    if isinstance(plugin, ModuleType):
        register = getattr(plugin, "register_lint_rules", None)
        if callable(register):
            _ = register(registry)
            return
    raise TypeError(
        f"Lint plugin {label!r} must be a callable or expose register_lint_rules(registry)"
    )


def _load_plugin_module(specifier: str) -> ModuleType:
    path = Path(specifier)
    if path.suffix == ".py" or path.exists():
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"Lint plugin path does not name a file: {specifier}")
        module_name = f"_flowmark_lint_plugin_{abs(hash(str(resolved)))}"
        module_spec = importlib.util.spec_from_file_location(module_name, resolved)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"Could not load lint plugin from {resolved}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        try:
            module_spec.loader.exec_module(module)
        except Exception:
            _ = sys.modules.pop(module_name, None)
            raise
        return module
    return importlib.import_module(specifier)


def load_lint_plugins(
    registry: RuleRegistry,
    plugins: Iterable[str] = (),
    *,
    discover_entry_points: bool = True,
) -> None:
    """Load installed and explicitly configured lint extensions."""

    if discover_entry_points:
        for entry_point in metadata.entry_points(group="flowmark.lint_rules"):
            plugin = cast(object, entry_point.load())
            _register_plugin_object(registry, plugin, entry_point.name)

    for specifier in dict.fromkeys(plugins):
        module = _load_plugin_module(specifier)
        _register_plugin_object(registry, module, specifier)


def parse_rule_setting(value: object) -> RuleSetting:
    """Normalize a TOML/JSON rule setting."""

    if isinstance(value, bool):
        return RuleSetting(level=RuleLevel.WARNING if value else RuleLevel.OFF)
    if isinstance(value, str):
        return RuleSetting(level=RuleLevel(value))
    if isinstance(value, Mapping):
        typed = cast(Mapping[object, object], value)
        level_value = typed.get("level")
        level = None if level_value is None else RuleLevel(str(level_value))
        options = {str(key): item for key, item in typed.items() if key != "level"}
        return RuleSetting(level=level, options=options)
    raise ValueError(
        "Lint rule setting must be a level string, boolean, or table with a 'level' key"
    )


def normalize_rule_settings(
    values: Mapping[str, object] | None,
) -> dict[str, RuleSetting]:
    if values is None:
        return {}
    return {str(name): parse_rule_setting(value) for name, value in values.items()}


def effective_rule_level(
    rule: LintRule | None,
    setting: RuleSetting | None,
    fallback: str,
) -> RuleLevel:
    if setting is not None and setting.level is not None:
        return setting.level
    if rule is not None:
        return rule.default_level
    return RuleLevel(fallback)


def apply_rule_policy(
    findings: Iterable[RuleFinding],
    registry: RuleRegistry,
    settings: Mapping[str, RuleSetting],
) -> list[RuleFinding]:
    """Apply named-rule enablement and severity overrides to findings."""

    result: list[RuleFinding] = []
    for finding in findings:
        level = effective_rule_level(
            registry.get(finding.rule),
            settings.get(finding.rule),
            finding.severity,
        )
        if level == RuleLevel.OFF:
            continue
        result.append(
            RuleFinding(
                rule=finding.rule,
                severity=level.value,
                message=finding.message,
                start=finding.start,
                end=finding.end,
                suggestions=finding.suggestions,
                data=finding.data,
            )
        )
    return result


def run_registered_rules(
    context: RuleContext,
    registry: RuleRegistry,
    settings: Mapping[str, RuleSetting],
) -> list[RuleFinding]:
    """Run every enabled registered rule which provides a check function."""

    findings: list[RuleFinding] = []
    for rule in registry.rules():
        if rule.check is None:
            continue
        setting = settings.get(rule.name)
        level = effective_rule_level(rule, setting, rule.default_level.value)
        if level == RuleLevel.OFF:
            continue
        options: Mapping[str, object] = (
            _empty_mapping() if setting is None else setting.options
        )
        for finding in rule.check(context, options):
            if finding.rule != rule.name:
                raise ValueError(
                    f"Rule {rule.name!r} emitted finding for {finding.rule!r}"
                )
            findings.append(
                RuleFinding(
                    rule=finding.rule,
                    severity=level.value,
                    message=finding.message,
                    start=finding.start,
                    end=finding.end,
                    suggestions=finding.suggestions,
                    data=finding.data,
                )
            )
    return findings


def validate_rule_settings(
    registry: RuleRegistry,
    settings: Mapping[str, RuleSetting],
) -> None:
    """Reject misspelled/unavailable configured rule ids."""

    unknown = sorted(set(settings) - registry.rule_names())
    if unknown:
        raise ValueError("Unknown lint rule(s): " + ", ".join(unknown))


__all__ = (
    "LintPlugin",
    "LintRule",
    "RuleCheck",
    "RuleContext",
    "RuleFinding",
    "RuleLevel",
    "RuleRegistry",
    "RuleSetting",
    "Suggestion",
    "apply_rule_policy",
    "effective_rule_level",
    "load_lint_plugins",
    "normalize_rule_settings",
    "parse_rule_setting",
    "run_registered_rules",
    "validate_rule_settings",
)

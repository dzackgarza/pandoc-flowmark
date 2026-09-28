"""Packaging entrypoint tests."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_cli_entrypoints() -> None:
    """The package installs the formatter and linter commands."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]

    assert scripts["flowmark"] == "flowmark.cli:main"
    assert scripts["flowmark-lint"] == "flowmark.lint_cli:main"


def test_source_archive_has_explicit_dynamic_version_fallback() -> None:
    """A copied/submodule-exported source tree must build without .git metadata."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["tool"]["uv-dynamic-versioning"]["fallback-version"] == "0.0.0"

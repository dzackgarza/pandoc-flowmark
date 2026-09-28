"""
Config file discovery, and the precedence of a config file against the CLI.

Precedence is explicit flags > config file > `--auto` preset > built-in defaults.
Each precedence test runs the real CLI in a directory holding a real config file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowmark.cli import main
from flowmark.config import ConfigError, find_config_file, load_lint_config

QUOTED = 'He said "hello" to them.\n'

QUOTED_WITH_TIGHT_LIST = 'He said "hello" to them.\n\n- one\n- two\n'

LONG_SENTENCE = (
    "The quick brown fox jumps over the lazy dog again and again. It rests.\n"
)


def _format_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str, doc: str, *flags: str
) -> str:
    """Write `config` and `doc` into `tmp_path`, format the doc in place from there."""
    (tmp_path / "flowmark.toml").write_text(config)
    doc_path = tmp_path / "doc.md"
    doc_path.write_text(doc)
    monkeypatch.chdir(tmp_path)
    assert main(["--inplace", "--nobackup", *flags, "doc.md"]) == 0
    return doc_path.read_text()


def test_find_config_flowmark_toml(tmp_path: Path) -> None:
    config_file = tmp_path / "flowmark.toml"
    config_file.write_text("[formatting]\nwidth = 100\n")
    result = find_config_file(tmp_path)
    assert result == config_file


def test_find_config_dot_flowmark_toml_takes_precedence(tmp_path: Path) -> None:
    (tmp_path / "flowmark.toml").write_text("[formatting]\nwidth = 100\n")
    dot_config = tmp_path / ".flowmark.toml"
    dot_config.write_text("[formatting]\nwidth = 80\n")
    result = find_config_file(tmp_path)
    assert result == dot_config


def test_find_config_pyproject_toml(tmp_path: Path) -> None:
    config_file = tmp_path / "pyproject.toml"
    config_file.write_text("[tool.flowmark]\nwidth = 100\n")
    result = find_config_file(tmp_path)
    assert result == config_file


def test_find_config_pyproject_without_section_skipped(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    result = find_config_file(tmp_path)
    assert result is None


def test_find_config_walks_up(tmp_path: Path) -> None:
    config_file = tmp_path / "flowmark.toml"
    config_file.write_text("[formatting]\nwidth = 100\n")
    subdir = tmp_path / "sub" / "deep"
    subdir.mkdir(parents=True)
    result = find_config_file(subdir)
    assert result == config_file


def test_find_config_none_when_missing(tmp_path: Path) -> None:
    result = find_config_file(tmp_path)
    assert result is None


def test_explicit_flag_beats_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _format_in_place(
        tmp_path, monkeypatch, "smartquotes = true\n", QUOTED, "--no-smartquotes"
    )
    assert out == QUOTED


def test_config_beats_auto_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #19 config: `--auto` must not discard settings the config file states."""
    out = _format_in_place(
        tmp_path,
        monkeypatch,
        '[formatting]\nlist-spacing = "preserve"\nsmartquotes = false\n',
        QUOTED_WITH_TIGHT_LIST,
        "--auto",
    )
    assert out == QUOTED_WITH_TIGHT_LIST


def test_auto_preset_beats_defaults_under_an_unrelated_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` turns on smart quotes; the config's width still applies."""
    out = _format_in_place(
        tmp_path,
        monkeypatch,
        "width = 40\n",
        QUOTED.removesuffix("\n") + " " + LONG_SENTENCE,
        "--auto",
    )
    assert out == (
        "He said “hello” to them.\n"
        "The quick brown fox jumps over the lazy\n"
        "dog again and again.\n"
        "It rests.\n"
    )


def test_config_width_beats_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config width wraps each sentence; it is not dropped by semantic breaks."""
    out = _format_in_place(tmp_path, monkeypatch, "width = 40\n", LONG_SENTENCE)
    assert out == (
        "The quick brown fox jumps over the lazy\ndog again and again.\nIt rests.\n"
    )


def test_config_respect_gitignore_false_lists_ignored_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "flowmark.toml").write_text(
        "[file-discovery]\nrespect-gitignore = false\n"
    )
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "found.md").write_text("# Found\n")
    monkeypatch.chdir(tmp_path)
    assert main(["--list-files", "."]) == 0
    listed = [Path(line).name for line in capsys.readouterr().out.split()]
    assert listed == ["found.md"]


@pytest.mark.parametrize(
    "config",
    [
        "colour = 60\n",
        "[formatting]\nsemantic = true\nsmart-quotes = true\n",
        "this is not toml [[[\n",
        'width = "wide"\n',
        'list-spacing = "bogus"\n',
    ],
)
def test_bad_config_fails_without_formatting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str
) -> None:
    (tmp_path / "flowmark.toml").write_text(config)
    doc_path = tmp_path / "doc.md"
    doc_path.write_text(QUOTED)
    monkeypatch.chdir(tmp_path)
    assert main(["--inplace", "--smartquotes", "doc.md"]) == 1
    assert doc_path.read_text() == QUOTED


def test_lint_table_is_read_by_the_linter_and_ignored_by_the_formatter(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "flowmark.toml"
    config_file.write_text(
        "[formatting]\nwidth = 88\n\n"
        "[lint]\n"
        'plugins = ["example_plugin"]\n'
        "max-line-length = 97\n"
        "discover-plugins = false\n"
        "\n"
        "[lint.rules]\n"
        '"heading/increment" = "off"\n'
        '"custom/example" = { level = "error", threshold = 3 }\n'
        "\n"
        "[lint.context]\n"
        'workspace = "/tmp/workspace"\n'
    )
    lint = load_lint_config(config_file)
    assert lint.plugins == ("example_plugin",)
    assert lint.max_line_length == 97
    assert lint.discover_plugins is False
    assert lint.rules == {
        "heading/increment": "off",
        "custom/example": {"level": "error", "threshold": 3},
    }
    assert lint.context == {"workspace": "/tmp/workspace"}


def test_unknown_lint_key_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "flowmark.toml"
    config_file.write_text('[lint]\nplugin = ["typo"]\n')
    with pytest.raises(ConfigError):
        _ = load_lint_config(config_file)

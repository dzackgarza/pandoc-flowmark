"""Regenerate src/flowmark/data/texstudio-command-index.json from TeXstudio's CWL files.

TeXstudio's completion corpus (https://github.com/texstudio-org/texstudio,
directory ``completion/``) lists, per package or class, the control sequences it
defines and the other CWL files it ``#include:``s. TeXstudio uses the same files
for completion and for its valid-command check. This script records each CWL
file's commands and includes at the upstream HEAD commit.

Run with ``uv run python devtools/update_texstudio_command_index.py``.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

REPOSITORY = "https://github.com/texstudio-org/texstudio.git"
CORE = ["tex", "latex-document", "latex-dev"]
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "src/flowmark/data/texstudio-command-index.json"
)
COMMAND = re.compile(r"^\\[A-Za-z@]+")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def main() -> None:
    commit = git("ls-remote", REPOSITORY, "HEAD").split()[0]
    with tempfile.TemporaryDirectory(prefix="flowmark-texstudio-cwl-") as temporary:
        checkout = Path(temporary) / "texstudio"
        _ = git(
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            REPOSITORY,
            str(checkout),
        )
        _ = git("-C", str(checkout), "sparse-checkout", "set", "completion")
        if git("-C", str(checkout), "rev-parse", "HEAD").strip() != commit:
            _ = git(
                "-C",
                str(checkout),
                "fetch",
                "--quiet",
                "--depth",
                "1",
                "origin",
                commit,
            )
            _ = git("-C", str(checkout), "checkout", "--quiet", "--detach", commit)

        files = sorted(
            (checkout / "completion").glob("*.cwl"), key=lambda path: path.name
        )
        packages: dict[str, dict[str, list[str]]] = {}
        for file in files:
            commands: set[str] = set()
            includes: list[str] = []
            for raw_line in file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                line = raw_line.strip()
                if line.startswith("#include:"):
                    dependency = (
                        line.removeprefix("#include:").strip().removesuffix(".cwl")
                    )
                    if dependency and dependency not in includes:
                        includes.append(dependency)
                    continue
                if match := COMMAND.match(line):
                    commands.add(match.group(0))
            packages[file.stem] = {"commands": sorted(commands), "includes": includes}

    payload = {
        "schema": 1,
        "source": {"repository": REPOSITORY, "commit": commit},
        "core": CORE,
        "packages": packages,
    }
    _ = OUTPUT.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(f"Updated {OUTPUT.name} from TeXstudio {commit} ({len(files)} CWL files)")


if __name__ == "__main__":
    main()

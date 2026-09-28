"""Citation keys of bibliography files, read with Pandoc's own bibliography readers.

Pandoc's citeproc chooses a reader by file extension (Pandoc manual, "Specifying
bibliographic data"): ``.bib`` is BibLaTeX, ``.bibtex`` is BibTeX, ``.json`` is
CSL JSON, ``.yaml`` is CSL YAML and ``.ris`` is RIS. Converting a file to CSL
JSON with the same reader gives exactly the keys an export of the document can
resolve.

Reading a large bibliography takes Pandoc seconds, so the keys of each file are
cached on disk and re-read only when the file's size or modification time
changes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast

from platformdirs import user_cache_path

from flowmark.pandoc_reader import pandoc_executable

PANDOC_BIBLIOGRAPHY_READERS = {
    ".bib": "biblatex",
    ".bibtex": "bibtex",
    ".json": "csljson",
    ".yaml": "markdown",
    ".ris": "ris",
}

_CACHE_DIR = user_cache_path("flowmark") / "bibliography-keys"


def bibliography_keys(path: Path) -> frozenset[str]:
    """Return the citation keys defined in the bibliography file at ``path``."""

    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    cache_file = _CACHE_DIR / (
        hashlib.sha256(str(resolved).encode()).hexdigest() + ".json"
    )
    if cache_file.is_file():
        cached = cast(dict[str, object], json.loads(cache_file.read_text()))
        if (
            cached.get("mtime_ns") == stat.st_mtime_ns
            and cached.get("size") == stat.st_size
        ):
            return frozenset(cast(list[str], cached["keys"]))

    keys = _read_keys(resolved)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _ = cache_file.write_text(
        json.dumps(
            {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "keys": sorted(keys)}
        )
    )
    return keys


def _read_keys(path: Path) -> frozenset[str]:
    reader = PANDOC_BIBLIOGRAPHY_READERS.get(path.suffix.casefold())
    if reader is None:
        raise ValueError(
            f"Unsupported bibliography format: {path}. Pandoc reads "
            + ", ".join(PANDOC_BIBLIOGRAPHY_READERS)
            + " files."
        )
    pandoc = pandoc_executable()
    completed = subprocess.run(
        [pandoc, "-f", reader, "-t", "csljson", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"Pandoc could not read bibliography {path}: {completed.stderr.strip()}"
        )
    entries = cast(list[dict[str, object]], json.loads(completed.stdout))
    return frozenset(str(entry["id"]) for entry in entries)


__all__ = ("PANDOC_BIBLIOGRAPHY_READERS", "bibliography_keys")

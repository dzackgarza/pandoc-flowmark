<!-- Generated from docs/shared/flowmark-readme-shared.md by `make readme`; edit that
file, not this one.
-->

# pandoc-flowmark

[![CI](https://github.com/dzackgarza/pandoc-flowmark/actions/workflows/ci.yml/badge.svg)](https://github.com/dzackgarza/pandoc-flowmark/actions/workflows/ci.yml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

pandoc-flowmark is a formatter and linter for
[Pandoc Markdown](https://pandoc.org/MANUAL.html#pandocs-markdown). It reads every
document with one Pandoc reader, edits only the source ranges that the reader reports,
and refuses to write a result whose Pandoc parse means something different.
It installs the `flowmark` and `flowmark-lint` commands and the `flowmark` Python package.

It started as a fork of [jlevy/flowmark](https://github.com/jlevy/flowmark), a
CommonMark-based formatter.

## Installing

Flowmark needs the `pandoc-flowmark` executable on `PATH`: a static Linux build of the
Pandoc fork, published as a release asset of
[dzackgarza/pandoc](https://github.com/dzackgarza/pandoc/releases).
Set `FLOWMARK_PANDOC` to use an executable with another name or path.

```shell
gh release download -R dzackgarza/pandoc -p pandoc-flowmark
install -m755 pandoc-flowmark ~/.local/bin/pandoc-flowmark
uv tool install git+https://github.com/dzackgarza/pandoc-flowmark
```

Then:

```shell
flowmark --help
```

* * *

{{ shared_docs_body }}

<!-- Generated from docs/shared/flowmark-readme-shared.md by `make readme`; edit that
file, not this one.
-->

# flowmark

[![CI](https://github.com/dzackgarza/pandoc-flowmark/actions/workflows/ci.yml/badge.svg)](https://github.com/dzackgarza/pandoc-flowmark/actions/workflows/ci.yml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

## pandoc-flowmark

> [!NOTE]
> This repository is a fork of [jlevy/flowmark](https://github.com/jlevy/flowmark) that
> parses Markdown only with a Pandoc reader. Its output differs from upstream Flowmark
> and from the [flowmark-rs](https://github.com/jlevy/flowmark-rs) port wherever Pandoc
> Markdown and CommonMark read a document differently.

## Installing

Flowmark needs the `pandoc-flowmark` executable on `PATH`: a static Linux build of the
Pandoc fork, published as a release asset of
[dzackgarza/pandoc](https://github.com/dzackgarza/pandoc/releases).
Set `FLOWMARK_PANDOC` to use an executable with another name or path.

```shell
gh release download flowmark-3.10.2-4 -R dzackgarza/pandoc -p pandoc-flowmark
install -m755 pandoc-flowmark ~/.local/bin/pandoc-flowmark
uv tool install git+https://github.com/dzackgarza/pandoc-flowmark
```

Then:

```shell
flowmark --help
```

Primary command: `flowmark`. Alias available in this repo: `flowmark-py`.

* * *

{{ shared_docs_body }}

# Development

## Setting Up uv

This project is set up to use [uv](https://docs.astral.sh/uv/) to manage Python and dependencies.
First, be sure you [have uv installed](https://docs.astral.sh/uv/getting-started/installation/).

Then [fork the jlevy/flowmark repo](https://github.com/jlevy/flowmark/fork) (having your own fork will make it easier to contribute) and [clone it](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository).

## Basic Developer Workflows

The `Makefile` simply offers shortcuts to `uv` commands for developer convenience.
(For clarity, GitHub Actions don’t use the Makefile and just call `uv` directly.)

```shell
# First, install all dependencies and set up your virtual environment.
# This simply runs `uv sync --all-extras` to install all packages,
# including dev dependencies and optional dependencies.
make install

# Run uv sync, lint, and test (and also generate agent rules):
make

# Regenerate README.md from docs/shared/flowmark-readme-shared.md and format it:
make readme

# Build wheel:
make build

# Lint, typecheck, and test through the global QC tiers (ai-review-ci).
# The pre-commit and pre-push hooks run the first two; the push and CI tiers add
# the golden tests.
just test-commit
just test-push
just test-ci

# Run tests:
make test

# Run tryscript golden tests only:
make test-golden

# Run tryscript coverage/quality gates only:
make test-golden-coverage

# Delete all the build artifacts:
make clean

# Upgrade dependencies to compatible versions:
make upgrade

# To run tests by hand:
uv run pytest   # all tests
uv run pytest -s src/module/some_file.py  # one test, showing outputs
npx tryscript@latest run tests/tryscript/*.tryscript.md  # tryscript suite
bash scripts/check-golden-coverage.sh  # quality/coverage checks

# Build and install current dev executables, to let you use your dev copies
# as local tools:
uv tool install --editable .

# Dependency management directly with uv:
# Add a new dependency:
uv add package_name
# Add a development dependency:
uv add --dev package_name
# Update to latest compatible versions (including dependencies on git repos):
uv sync --upgrade
# Update a specific package:
uv lock --upgrade-package package_name
# Update dependencies on a package:
uv add package_name@latest

# Run a shell within the Python environment:
uv venv
source .venv/bin/activate
```

See [uv docs](https://docs.astral.sh/uv/) for details.

## The Pandoc Reader

Flowmark parses Markdown only with `pandoc-flowmark`, a build of the Pandoc fork
[dzackgarza/pandoc](https://github.com/dzackgarza/pandoc), branch `flowmark-sourcepos`.
Formatting, linting, and verification all run it with the one reader format in
`src/flowmark/pandoc_reader.py` (`PANDOC_FORMAT`). The fork adds two Markdown reader
extensions:

- `sourcepos` wraps each block in a `Div` and each inline in a `Span` whose `data-pos`
  attribute is the node's source range (`line:column-line:column`, columns counted with
  tabs expanded to Pandoc's tab stop of 4). Nodes Pandoc re-parses from a string, such
  as table cells and superscripts, carry no range.
- `flowmark_tags` reads an unindented line holding one Jinja or Markdoc tag, comment,
  or variable, or one HTML comment, as its own block, so a list or table ends before it.

Each formatting pass in `src/flowmark/pandoc_source.py` reads the current text, edits
only at the ranges Pandoc reports, and hands the text to the next pass. Pandoc output
is cached by input text, so a pass that changes nothing costs the next one no Pandoc
run. `check_meaning_preserved` compares the Pandoc readings of the input and the result
and accepts only the named normalizations in `src/flowmark/pandoc_verify.py`.

Install the release build that CI pins:

```shell
just install-pandoc-flowmark
```

`FLOWMARK_PANDOC` names another executable, for example a local build of the fork.

## The Note Corpus

`tests/notes` holds excerpts of real notes on which flowmark reported a false
positive, missed a defect, or changed a document's meaning.
Each excerpt is copied verbatim from the note, with only the lines needed to
reproduce the behavior, and it fails on the flowmark build that had the problem.
`tests/test_note_corpus.py` pins every warning and error on each excerpt, checks that
formatting keeps its meaning, and checks each machine-applicable fix.

A defect found in a note is a report against flowmark.
Add the excerpt here and change flowmark; never repair the note by hand, because that
deletes the only copy of the real case.

## Agent Rules

See [.cursor/rules](.cursor/rules) for agent rules.
These are written for [Cursor](https://www.cursor.com/) but are also used by other agents because the Makefile will generate `CLAUDE.md` and `AGENTS.md` from the same rules.

```shell
make agent-rules
```

## IDE setup

If you use VSCode or a fork like Cursor or Windsurf, you can install the following extensions:

- [Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python)

- [Based Pyright](https://marketplace.visualstudio.com/items?itemName=detachhead.basedpyright) for type checking.
  Note that this extension works with non-Microsoft VSCode forks like Cursor.

## Publishing Releases

See [publishing.md](publishing.md) for instructions on publishing to PyPI.

## Documentation

- [uv docs](https://docs.astral.sh/uv/)

- [basedpyright docs](https://docs.basedpyright.com/latest/)

* * *

*This file was built with [simple-modern-uv](https://github.com/jlevy/simple-modern-uv).*

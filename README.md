<!-- Generated from docs/shared/flowmark-readme-shared.md by `make readme`; edit that
file, not this one.
-->

# pandoc-flowmark

[![CI](https://github.com/dzackgarza/pandoc-flowmark/actions/workflows/ci.yml/badge.svg)](https://github.com/dzackgarza/pandoc-flowmark/actions/workflows/ci.yml) [![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

pandoc-flowmark is a formatter and linter for [Pandoc Markdown](https://pandoc.org/MANUAL.html#pandocs-markdown).
It reads every document with one Pandoc reader, edits only the source ranges that the reader reports, and refuses to write a result whose Pandoc parse means something different.
It installs the `flowmark` and `flowmark-lint` commands and the `flowmark` Python package.

It started as a fork of [jlevy/flowmark](https://github.com/jlevy/flowmark), a CommonMark-based formatter.

## Installing

Flowmark needs the `pandoc-flowmark` executable on `PATH`: a static Linux build of the Pandoc fork, published as a release asset of [dzackgarza/pandoc](https://github.com/dzackgarza/pandoc/releases).
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

* * *

## Why Use Flowmark?

Flowmark is a Markdown auto-formatter, written in Python, designed for **better LLM workflows**, **clean git diffs**, and **flexible use from CLI, from IDEs, or as a library**.

With AI tools increasingly using Markdown, having consistent, diff-friendly formatting has become essential for modern writing, editing, and document processing workflows.
Normalizing Markdown formatting greatly improves collaborative editing and LLM workflows, especially when committing documents to git repositories.

You can use Flowmark as a CLI, as an autoformatter in your IDE, or as a Python library.

Flowmark reads Markdown with [Pandoc](https://pandoc.org/)’s Markdown reader and nothing else.
The reader is a fork, [dzackgarza/pandoc](https://github.com/dzackgarza/pandoc) (branch `flowmark-sourcepos`), whose `sourcepos` extension reports the source line and column of each block and inline node, and whose `flowmark_tags` extension reads a template tag line as its own block.
Every formatting step edits the source only at the ranges Pandoc reports, so text Flowmark does not change keeps its exact bytes, including raw TeX, attributes, and template tags.
Before writing, Flowmark parses the original and the result with the same reader and refuses to write if they mean different things, except for the style normalizations it names.

## Comparison With Other Formatters

Flowmark formats [Pandoc Markdown](https://pandoc.org/MANUAL.html#pandocs-markdown) with the extensions `fenced_divs`, `raw_tex`, `tex_math_dollars`, `tex_math_single_backslash`, `wikilinks_title_after_pipe`, `autolink_bare_uris`, and `flowmark_tags`.

The key differences from [other Markdown formatters](#why-another-markdown-formatter):

- Carefully chosen default formatting rules that are effective for use in editors/IDEs, in LLM pipelines, and also when paging through docs in a terminal.
  It parses and normalizes standard links and special characters, headings, tables, footnotes, and horizontal rules and performing Markdown-aware line wrapping.

- “Just works” support for pipe and grid tables, footnotes, fenced divs, TeX math and raw TeX, wikilinks, YAML frontmatter, template tags (Markdoc, Jinja, Nunjucks), and inline HTML comments.

- Advanced and customizable line-wrapping capabilities, including [semantic line breaks](#semantic-line-breaks), a feature that is especially helpful in allowing collaborative edits on a Markdown document while avoiding git conflicts.

- Optional [automatic smart quotes](#smart-quote-support) for professional-looking typography.

General philosophy:

- Be conservative about changes so that it is safe to run automatically on save or after any stage of a document pipeline.

- Be opinionated about sensible defaults but not dogmatic by preventing customization.
  You can adjust or disable most settings.
  And if you are using it as a library, you can fully control anything you want (including more complex things like custom line wrapping for HTML).

- Be as small and simple as possible, with few dependencies: the `pandoc-flowmark` executable, [`pathspec`](https://pypi.org/project/pathspec/), [`regex`](https://pypi.org/project/regex/), and [`strif`](https://github.com/jlevy/strif).

## Use Cases

The main ways to use Flowmark are:

- To **autoformat Markdown on save in VSCode/Cursor** or any other editor that supports running a command on save.
  See [below](#use-in-vscodecursor) for recommended VSCode/Cursor setup.

- As a **command line formatter** to format text or Markdown files using the `flowmark` command.

- As a **library to autoformat Markdown** from document pipelines.
  For example, it is great to normalize the outputs from LLMs to be consistent, or to run on the inputs and outputs of LLM transformations that edit text, so that the resulting diffs are clean.

- As a more powerful **drop-in replacement library for Python’s default [`textwrap`](https://docs.python.org/3/library/textwrap.html)** but with more options.
  It simplifies and generalizes that library, offering better control over **initial and subsequent indentation** and **when to split words and lines**, e.g. using a word splitter that won’t break lines within HTML tags, template tags (`{% %}`, `{# #}`, `{{ }}`), Markdown links (including links with multi-word text), inline code spans (`` `code with spaces` ``), or HTML comments.
  See [`wrap_paragraph_lines`](src/flowmark/linewrapping/text_wrapping.py).

## Semantic Line Breaks

> [!TIP]
> For an example of a Markdown document formatted with semantic line breaks, see [the Markdown source](https://github.com/dzackgarza/pandoc-flowmark/blob/main/README.md?plain=1) of this readme file.

Some Markdown auto-formatters never wrap lines, while others wrap at a fixed width.
By default, Flowmark does neither: it puts each sentence on its own line and sets no column limit.
This is a small change that can dramatically improve diff readability when collaborating or working with AI tools.

With `--width N`, sentences are split first, and a sentence longer than N is then wrapped to N. A line shorter than 20 characters is joined with the next sentence when both fit in N.

Pass `--no-semantic` to wrap paragraphs at a fixed width instead, as traditional formatters do.
The width is then **88 columns** unless `--width` is given.
The “[90-ish columns](https://youtu.be/esZLCuWs_2Y?si=lUj055ROI--6tVU8&t=1288)” compromise was popularized by Black and also works well for Markdown.
`--width 0` disables column wrapping in both modes.

This idea of **semantic line breaks**, which is breaking lines in ways that make sense logically when possible (much like with code) is an old one.
But it usually requires people to agree on how to break lines, which is both difficult and sometimes controversial.

However, now we are using versioned Markdown more than ever, it’s a good time to revisit this idea, as it can **make diffs in git much more readable**. The change may seem subtle but avoids having paragraphs reflow for very small edits, which does a lot to **minimize merge conflicts**.

Flowmark refines [traditional semantic line breaks](https://github.com/sembr/specification).
Instead of just allowing you to break lines as you wish, it auto-applies fixed conventions about likely sentence boundaries in a conservative and reasonable way.
It uses simple and fast **regex-based sentence splitting**. While not perfect, this works well for these purposes (and is much faster and simpler than a proper sentence parser like SpaCy).
It should work fine for English and many other Latin/Cyrillic languages, but hasn’t been tested on CJK. You can see some [old discussion](https://github.com/shurcooL/markdownfmt/issues/17) of this idea with the markdownfmt author.

To see the effect, run `flowmark --auto` on a document and edit and commit it.

Semantic line breaks are the default.
Turn them off with `--no-semantic`.

## Typographic Cleanups

### Smart Quote Support

Flowmark offers optional **automatic smart quotes** to convert \"non-oriented quotes\" to “oriented quotes” and apostrophes intelligently.

This is a robust way to ensure Markdown text can be converted directly to HTML with professional-looking typography.

Smart quotes are applied conservatively and won’t affect code blocks, so they don’t break code snippets.
It only applies them within single paragraphs of text, and only applies to \' and \" quote marks around regular text.
An apostrophe that pandoc pairs with a later quote mark stays straight.
In `the '90s, rock 'n' roll`, pandoc reads everything from the quote before `90s` to the quote after `n` as one quoted span, and curling either quote would remove that span.

This feature is enabled with the `--smartquotes` flag or the `--auto` convenience flag.

### Ellipsis Support

There is a similar feature for converting `...` to an ellipsis character `…` when it appears to be appropriate (i.e., not in code blocks and when adjacent to words or punctuation).

This feature is enabled with the `--ellipses` flag or the `--auto` convenience flag.

## Frontmatter Support

Because **YAML frontmatter** is common on Markdown files, any YAML frontmatter (content between `---` delimiters at the front of a file) is always preserved exactly.
YAML is not normalized.

> [!TIP]
> See the [frontmatter format](https://github.com/jlevy/frontmatter-format) repo for more discussion of YAML frontmatter and its benefits.

## Usage

Flowmark can be used as a library or as a CLI.

### Quick Start

```bash
# Format all Markdown files in current directory recursively
flowmark --auto .

# Format a single file in-place with all auto-formatting options
flowmark --auto README.md

# List files that would be formatted (without formatting)
flowmark --list-files .

# Format to stdout
flowmark README.md

# Format from stdin (use '-' explicitly)
echo "Some text" | flowmark -
```

### Batch Formatting

The simplest way to format all Markdown in a project:

```bash
flowmark --auto .
```

This recursively discovers all `.md` files, skips common non-content directories (`node_modules`, `.venv`, `build`, etc.), respects `.gitignore`, and formats everything in-place with semantic line breaks, smart quotes, ellipses, and cleanups.

### CLI Reference

The main flags:

| Flag | Description |
| --- | --- |
| `-o, --output FILE` | Output file (use `-` for stdout); only one input file may be given |
| `-w, --width WIDTH` | Line width. When not given: no limit with semantic line breaks, 88 with `--no-semantic` or `--plaintext`. 0 disables wrapping |
| `-p, --plaintext` | Process as plaintext (no Markdown parsing) |
| `-s, --semantic` | Semantic (sentence-based) line breaks (default: on; `--no-semantic` wraps to a column width) |
| `-c, --cleanups` | Safe cleanups (unbold headings, etc.) (default: off) |
| `--smartquotes` | Convert straight quotes to typographic quotes (default: off) |
| `--ellipses` | Convert `...` to `…` (default: off) |
| `--list-spacing` | List spacing: `loose` (default), `tight`, or `preserve`. Flowmark normalizes list spacing to one style, as it normalizes other formatting |
| `-i, --inplace` | Edit in place |
| `--nobackup` | Skip `.orig` backup with `--inplace` |
| `--auto` | All auto-formatting: `--inplace --nobackup --semantic --cleanups --smartquotes --ellipses`, beneath the config file and explicit flags. Requires file/directory args (use `.` for current directory) |

Each on/off flag also has a `--no-` form, for example `--no-smartquotes`, to override a config file or `--auto`.

File discovery flags:

| Flag | Description |
| --- | --- |
| `--list-files` | Print resolved file paths, don’t format |
| `--extend-include PATTERN` | Additional file patterns (e.g., `*.mdx`) |
| `--exclude PATTERN` | Replace all default exclusions |
| `--extend-exclude PATTERN` | Add to default exclusions (e.g., `drafts/`) |
| `--no-respect-gitignore` | Disable `.gitignore` integration |
| `--force-exclude` | Apply exclusions to explicitly-named files |
| `--files-max-size BYTES` | Skip files larger than this (default: 1 MiB) |

## File Discovery

When you pass a directory to Flowmark (e.g., `flowmark --auto .`), it recursively discovers files using a smart filter pipeline:

1. **Default includes**: Only `*.md` files by default.
   Use `--extend-include "*.mdx"` to add patterns.

2. **Default exclusions**: ~45 directories are automatically skipped, including `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `build`, `dist`, `.tox`, `.nox`, `.idea`, `.vscode`, `vendor`, `third_party`, and more.
   These directories are pruned during traversal for performance.

3. **`.gitignore` integration**: Enabled by default.
   Reads `.gitignore` at every directory level during traversal.
   Disable with `--no-respect-gitignore`.

4. **`.flowmarkignore`**: A tool-specific ignore file using gitignore syntax.
   Place it in your project root to exclude paths specific to Flowmark formatting.

5. **Max file size**: Files over 1 MiB are skipped by default.
   Change with `--files-max-size` (0 = no limit).

### Customizing Includes and Excludes

```bash
# Also format .mdx files
flowmark --auto --extend-include "*.mdx" .

# Skip a specific directory
flowmark --auto --extend-exclude "drafts/" .

# Replace ALL default exclusions with your own
flowmark --auto --exclude "my_custom_dir/" .

# Debug: see exactly which files would be formatted
flowmark --list-files .
```

### Glob Patterns

When passing glob patterns as arguments, **always quote them** so Flowmark can handle expansion internally:

```bash
# Correct: Flowmark expands the glob (** works for recursive matching)
flowmark --auto 'docs/**/*.md'

# Risky: shell may expand ** incorrectly if globstar is off (the default in bash)
flowmark --auto docs/**/*.md
```

Without quoting, the shell may expand `**` as a single `*` (matching only one directory level) or pass nothing if there are no matches.
Flowmark uses Python’s `pathlib.Path.glob()` internally, which always supports `**` for recursive matching regardless of shell settings.

Note: The `--extend-include` and `--extend-exclude` flags use gitignore-style patterns (e.g., `*.mdx`, `drafts/`), not shell globs.

### Symlinks

During recursive directory traversal, **symlinks are not followed**. This prevents infinite loops from circular symlinks and avoids accidentally formatting files outside the project tree.

However, if you pass a symlink **explicitly** as an argument (e.g., `flowmark --auto link-to-readme.md`), the symlink is resolved and the target file is processed.

## Configuration

Flowmark supports TOML-based configuration files.
It searches for config files in this order (first match wins, walking up directories):

1. `.flowmark.toml`

2. `flowmark.toml`

3. `pyproject.toml` (only if it has a `[tool.flowmark]` section)

A config key is the long name of a CLI flag, for example `list-spacing` for `--list-spacing`. Unparsable TOML, an unknown key, or a value of the wrong type is an error: Flowmark names the file and key and formats nothing.

### Example Config

```toml
# flowmark.toml (or .flowmark.toml)

[formatting]
width = 100
semantic = true
smartquotes = true
ellipses = true
list-spacing = "preserve"  # opt out of the loose-spacing default

[file-discovery]
extend-include = ["*.mdx", "*.markdown"]
extend-exclude = ["drafts/", "archive/"]
files-max-size = 2097152  # 2 MiB
```

Or in `pyproject.toml`:

```toml
[tool.flowmark]
width = 100
semantic = true
extend-exclude = ["drafts/"]
```

### Precedence

An explicit flag overrides the config file.
The config file overrides the `--auto` preset.
The `--auto` preset overrides the built-in defaults.

So a config file with `list-spacing = "preserve"` or `smartquotes = false` keeps that setting under `--auto`, and `flowmark --auto --no-smartquotes README.md` formats without smart quotes.
Repeatable list flags (`--extend-include`, `--exclude`, `--extend-exclude`) add to the lists in the config file.

## Use in VSCode/Cursor

You can use Flowmark to auto-format Markdown on save in VSCode or Cursor.
Install the “Run on Save” (`emeraldwalk.runonsave`) extension.
Then add to your `settings.json`:

```json
  "emeraldwalk.runonsave": {
    "commands": [
        {
            "match": "(\\.md|\\.md\\.jinja|\\.mdc)$",
            "cmd": "flowmark --auto ${file}"
        }
    ]
  }
```

The `--auto` option sets `--inplace --nobackup --semantic --cleanups --smartquotes --ellipses`; a config file or an explicit flag overrides any of them.

For batch formatting an entire project, use `flowmark --auto .` from the terminal.

## Agent Use (Claude Code and Other AI Coding Agents)

Flowmark can be installed as a skill for Claude Code and other AI coding agents, enabling automatic Markdown formatting in agent workflows.

### Install the Skill

```bash
# Install globally (available to all projects)
flowmark --install-skill

# Or install to current project only
flowmark --install-skill --agent-base ./.claude
```

After installation, Claude Code will automatically recognize when to use Flowmark for Markdown formatting tasks.

### Agent Skill Options

| Flag | Description |
| --- | --- |
| `--skill` | Print skill instructions (SKILL.md content) |
| `--install-skill` | Install Claude Code skill for flowmark |
| `--agent-base DIR` | Agent config directory (default: ~/.claude) |
| `--docs` | Print full documentation |

### Manual Usage in Agents

If you prefer to use Flowmark manually within agent sessions:

```bash
# Format with all auto-formatting options
flowmark --auto README.md

# Preview formatted output
flowmark README.md

# Format LLM output (use '-' for stdin)
echo "$llm_output" | flowmark -
```

## Why Another Markdown Formatter?

There are several other Markdown auto-formatters:

- [markdownfmt](https://github.com/shurcooL/markdownfmt) is one of the oldest and most popular Markdown formatters and works well for basic formatting.

- [mdformat](https://github.com/executablebooks/mdformat) is probably the closest alternative to Flowmark and it also uses Python.
  It preserves line breaks in order to support semantic line breaks, but does not auto-apply them as Flowmark does and has somewhat different features.

- [Prettier](https://prettier.io/blog/2017/11/07/1.8.0) is the ubiquitous Node formatter that handles Markdown/MDX

- [dprint-plugin-markdown](https://github.com/dprint/dprint-plugin-markdown) is a Markdown plugin for dprint, the fast Rust/WASM engine

- Rule-based linters like [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2) catch violations and sometimes fix them, but they do not reformat a document.

- Finally, the [remark ecosystem](https://github.com/remarkjs/remark) is by far the most powerful library ecosystem for building your own Markdown tooling in JavaScript/TypeScript.
  You can build auto-formatters with it but there isn’t one that’s broadly used as a CLI tool.

None of these reads Pandoc Markdown with Pandoc’s own reader, so none of them can guarantee that formatting keeps the meaning Pandoc gives a document.
None of them applies semantic line breaks automatically.

## Pandoc-aware linting

Flowmark also ships a standalone linter over the same semantic Markdown parser used by the formatter.
It understands Flowmark’s Pandoc-oriented constructs (including math, raw TeX, fenced divs, definition lists, tables, and footnotes) before applying style checks, so TeX underscores and asterisks are not reinterpreted as Markdown emphasis.

```bash
flowmark-lint README.md
flowmark-lint --format json --exit-zero - < document.md
```

The Python API is `flowmark.lint_text()`. Diagnostics use 1-based source coordinates and stable rule ids.
The default rule layer checks structural/semantic failures that a formatter cannot safely infer away: heading hierarchy/duplicates, reference and footnote integrity, malformed or empty links, local fragments and local-file targets, image alt text, fenced-code language/tabs/boundaries, frontmatter integrity, duplicate Pandoc ids, malformed attributes, and unclosed fenced-div/math/TeX constructs.
It also reports mathematics written outside `$...$`: `math/outside-math-mode` for TeX notation in prose (`x_0`, `R^n`, `\sum`), which pandoc reads as emphasis delimiters, plain text, or raw TeX that HTML output drops, and `math/unicode-symbol` for Unicode math symbols (`⊗`, `→`, `α`) anywhere except a fenced block that names its language.
`math/backslash-delimiter` reports `\(...\)` and `\[...\]`, which pandoc’s `markdown` reads as a literal parenthesis or bracket, not math.
`pandoc/ambiguous-input` adds the high-confidence ambiguity checks from Flowmark’s preflight, while `format/canonical` reports remaining source ranges that differ from Flowmark’s canonical rendering.
The linter does not edit files.

Pure house-style policies are opt-in instead of being treated as Markdown correctness:

```bash
flowmark-lint --style bare-url --style heading-punctuation README.md
flowmark-lint --style unordered-list-marker --style fence-marker README.md
flowmark-lint --style require-h1 --style no-inline-html README.md
flowmark-lint --max-line-length 100 README.md
```

Editor/stdin clients can supply `--source-path PATH` so relative links and cross-file Markdown fragments are checked against the document’s real location.

## Project Docs

For development workflows, see [development.md](docs/development.md).

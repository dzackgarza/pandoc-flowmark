---
sandbox: true
env:
  NO_COLOR: "1"
  LC_ALL: C
  COLUMNS: "80"
path:
  - $TRYSCRIPT_GIT_ROOT/.venv/bin
before: |
  printf '## **Title**\n\nHe said "hi"... This sentence is long enough that it runs past eighty-eight columns when it stays on one line. Short.\n\n- one\n- two\n' > doc.md
---

# Help Output

## H1: Help tagline

```console
$ flowmark --help | grep -F "Flowmark: Better auto-formatting for Markdown and plaintext"
Flowmark: Better auto-formatting for Markdown and plaintext
```

## H2: Common usage examples are present

```console
$ flowmark --help | grep -F "flowmark --auto README.md"
  flowmark --auto README.md
```

```console
$ flowmark --help | grep -F "flowmark --auto docs/"
  flowmark --auto docs/
```

```console
$ flowmark --help | grep -F "flowmark --auto ."
  flowmark --auto .
```

```console
$ flowmark --help | grep -F "flowmark --list-files ."
  flowmark --list-files .
```

## H3: Agent guidance is explicit

```console
$ flowmark --help | grep -Fx "  flowmark --skill"
  flowmark --skill
```

```console
$ flowmark --help | grep -F "Agents should run"
  Agents should run `flowmark --skill` for full Flowmark usage guidance.
```

## H4: Full docs are via --docs

```console
$ flowmark --help | grep -F "flowmark --docs"
Use `flowmark --docs` for full documentation.
```

## H5: Full help text

The help is the contract for every default. H6 checks each stated default against the
runtime.

```console
$ flowmark --help
usage: flowmark [-h] [-o OUTPUT | -i | --auto] [-w WIDTH] [-p]
                [-s | --semantic | --no-semantic]
                [-c | --cleanups | --no-cleanups]
                [--smartquotes | --no-smartquotes]
                [--ellipses | --no-ellipses] [--verify | --no-verify]
                [--list-spacing {preserve,loose,tight}] [--nobackup]
                [--extend-include PATTERN] [--exclude PATTERN]
                [--extend-exclude PATTERN]
                [--respect-gitignore | --no-respect-gitignore]
                [--force-exclude | --no-force-exclude] [--list-files]
                [--files-max-size BYTES] [--version] [--skill]
                [--install-skill] [--agent-base DIR] [--docs]
                [files ...]

Flowmark: Better auto-formatting for Markdown and plaintext

By default, Flowmark puts each sentence on its own line (semantic line breaks).
Use --no-semantic to wrap paragraphs to a column width instead.

positional arguments:
  files                 Input files or directories (required; use '-' for
                        stdin, '.' for current directory)

options:
  -h, --help            show this help message and exit
  -o, --output OUTPUT   Output file (use '-' for stdout, the default). Only
                        one input file may be given; not with --inplace or
                        --auto
  -w, --width WIDTH     Line width to wrap to, or 0 to disable line wrapping.
                        When not given: with semantic line breaks, no limit
                        (each sentence gets its own line); with --no-semantic
                        or --plaintext, 88. With semantic line breaks and -w
                        N, sentences are split first, and a sentence longer
                        than N is then wrapped to N; a line shorter than 20
                        characters is joined with the next sentence when both
                        fit in N
  -p, --plaintext       Process as plaintext (no Markdown parsing)
  -s, --semantic, --no-semantic
                        Semantic line breaks: put each sentence on its own
                        line (default: True). --no-semantic wraps paragraphs
                        to a column width instead (only applies to Markdown
                        mode)
  -c, --cleanups, --no-cleanups
                        Enable (safe) cleanups for common issues like
                        accidentally boldfaced section headers (default:
                        False; only applies to Markdown mode)
  --smartquotes, --no-smartquotes
                        Convert straight quotes to typographic (curly) quotes
                        and apostrophes (default: False; only applies to
                        Markdown mode)
  --ellipses, --no-ellipses
                        Convert three dots (...) to ellipsis character (…)
                        with normalized spacing (default: False; only applies
                        to Markdown mode)
  --verify, --no-verify
                        Check with pandoc that the output parses to the same
                        AST as the input, and fail without writing if it does
                        not (default: True). This is a safety gate: it catches
                        any bug where flowmark would change a document's
                        meaning rather than just its spelling. Requires the
                        `pandoc` binary on PATH. Pass --no-verify to skip the
                        check and write anyway (only applies to Markdown mode)
  --list-spacing {preserve,loose,tight}
                        List spacing: 'loose' puts a blank line between all
                        items, 'tight' removes blank lines where possible,
                        'preserve' keeps each list as written (default:
                        loose). Flowmark normalizes list spacing to one style,
                        as it normalizes other formatting
  -i, --inplace         Edit the file in place
  --nobackup            Do not make a backup of the original file when using
                        --inplace
  --auto                Fully auto-format files in place: sets `--inplace
                        --nobackup --semantic --cleanups --smartquotes
                        --ellipses`. The config file and explicit flags
                        override these (e.g. `--auto --no-smartquotes`).
                        Requires at least one file or directory argument (use
                        '.' for current directory)
  --extend-include PATTERN
                        Additional file patterns to include (e.g., '*.mdx').
                        Can be repeated; adds to the config file's patterns
  --exclude PATTERN     Replace all default exclusion patterns. Can be
                        repeated; adds to the config file's patterns
  --extend-exclude PATTERN
                        Add to default exclusion patterns (e.g., 'drafts/').
                        Can be repeated; adds to the config file's patterns
  --respect-gitignore, --no-respect-gitignore
                        Skip files that .gitignore ignores (default: True)
  --force-exclude, --no-force-exclude
                        Apply exclusion patterns even to files named
                        explicitly on the command line (default: False)
  --list-files          Print resolved file paths without formatting. Requires
                        at least one file or directory argument (use '.' for
                        current directory)
  --files-max-size BYTES
                        Skip files larger than this size in bytes (0 = no
                        limit, default: 1048576)
  --version             Show version information and exit
  --skill               Print skill instructions (SKILL.md content) for Claude
                        Code
  --install-skill       Install Claude Code skill for flowmark
  --agent-base DIR      Agent config directory for skill installation
                        (default: ~/.claude)
  --docs                Print full documentation

Common usage:
  flowmark README.md
  flowmark --auto README.md
  flowmark --auto docs/
  flowmark --auto .
  flowmark --list-files .

Settings:
  An explicit flag overrides the config file (.flowmark.toml, flowmark.toml, or
  [tool.flowmark] in pyproject.toml, in this directory or a parent). The config
  file overrides the --auto preset, and --auto overrides the built-in defaults.

Agent usage:
  flowmark --skill
  Agents should run `flowmark --skill` for full Flowmark usage guidance.

Use `flowmark --docs` for full documentation.
```

## H6: The defaults stated in the help are the runtime defaults

`doc.md` has a bold heading, straight quotes, three dots, two sentences, one longer than
88 columns, and a tight list, so every stated default changes its output.

```console
$ flowmark doc.md
## **Title**

He said "hi"... This sentence is long enough that it runs past eighty-eight columns when it stays on one line.
Short.

- one

- two
```

```console
$ flowmark --semantic --no-cleanups --no-smartquotes --no-ellipses --list-spacing loose --verify doc.md > stated.md && flowmark doc.md | diff - stated.md && echo "same as the stated defaults"
same as the stated defaults
```

With semantic line breaks, no `-w` and `-w 0` are the same: no column limit.

```console
$ flowmark -w 0 doc.md > w0.md && flowmark doc.md | diff - w0.md && echo "same as -w 0"
same as -w 0
```

`-w 88` differs from no `-w`: the long sentence is then wrapped to 88.

```console
$ flowmark -w 88 doc.md
## **Title**

He said "hi"... This sentence is long enough that it runs past eighty-eight columns when
it stays on one line.
Short.

- one

- two
```

With `--no-semantic`, and with `--plaintext`, no `-w` is the same as `-w 88`.

```console
$ flowmark --no-semantic -w 88 doc.md > col88.md && flowmark --no-semantic doc.md | diff - col88.md && echo "same as -w 88"
same as -w 88
```

```console
$ flowmark -p -w 88 doc.md > plain88.md && flowmark -p doc.md | diff - plain88.md && echo "same as -w 88"
same as -w 88
```

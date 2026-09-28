---
name: flowmark
description: Auto-format and lint Pandoc Markdown with semantic line breaks, smart quotes, and diff-friendly output. Use for formatting Markdown files, normalizing LLM outputs, or when user mentions flowmark, markdown formatting, or semantic line breaks.
allowed-tools: Bash(flowmark:*), Bash(flowmark-lint:*), Read, Write
---
# Flowmark - Markdown Auto-Formatter

> **Full documentation: Run `flowmark --docs` for all options and usage.**

Auto-format Pandoc Markdown with semantic line breaks for clean git diffs and consistent
output. Flowmark reads each document with the `pandoc-flowmark` reader, which must be on
`PATH`, and refuses to write a result that Pandoc reads differently.

## Quick Start

**Format a file in place with all auto-formatting:**
```bash
flowmark --auto README.md
```

**Preview formatted output to stdout:**
```bash
flowmark README.md
```

## When to Use Flowmark

**Use flowmark for:**
- Auto-formatting Markdown on save or in pipelines
- Normalizing LLM-generated Markdown output
- Preparing documents for git with semantic line breaks
- Converting straight quotes to typographic quotes
- Consistent Markdown styling across a project

**Don’t use flowmark for:**
- Syntax highlighting or rendering (use a Markdown viewer)
- Converting between formats (use pandoc)

## Key Options

| Flag | Purpose |
| --- | --- |
| `--auto` | Format in-place with all improvements (semantic, cleanups, smartquotes, ellipses); a config file or explicit flag overrides each. Requires file/directory args (use `.` for current directory) |
| `--inplace`, `-i` | Edit file in place |
| `--semantic`, `-s` / `--no-semantic` | Semantic (sentence-based) line breaks, on by default; `--no-semantic` wraps to a column width |
| `--smartquotes` | Convert straight to curly quotes |
| `--ellipses` | Convert three dots to ellipsis character |
| `--width WIDTH` | Line width. When not given: no limit with semantic line breaks, 88 with `--no-semantic` or `--plaintext`. 0 disables wrapping |
| `--plaintext`, `-p` | Process as plain text instead of Markdown |
| `--list-spacing` | List spacing: loose (default), tight, or preserve |
| `--list-files` | Print resolved file paths, don’t format (useful for debugging) |
| `--extend-include PAT` | Additional file patterns (e.g., `*.mdx`) |
| `--extend-exclude PAT` | Add to default exclusions (e.g., `drafts/`) |
| `--files-max-size BYTES` | Skip files larger than this (default: 1 MiB, 0 = no limit) |

## Common Workflows

### Lint Without Writing

```bash
flowmark-lint docs/
```

### Format for Git

```bash
flowmark --auto *.md
git diff  # Review clean, semantic diffs
```

### Format LLM Output

```bash
echo "$llm_output" | flowmark -
```

### Batch Format

```bash
# Format all Markdown files in current directory recursively
flowmark --auto .

# List files that would be formatted (without formatting)
flowmark --list-files .
```

### Stdin/Stdout Processing

```bash
cat document.md | flowmark - > formatted.md
```

### VS Code/Cursor (Run on Save)

Install the `emeraldwalk.runonsave` extension and add this to `settings.json`:

```json
"emeraldwalk.runonsave": {
  "autoClearConsole": false,
  "commands": [
    {
      "match": "(\\.md|\\.md\\.jinja|\\.mdc)$",
      "cmd": "flowmark --auto ${file}"
    }
  ]
}
```

## Semantic Line Breaks

By default, Flowmark breaks lines at sentence boundaries instead of at fixed widths
(`--no-semantic` restores fixed-width wrapping). This produces cleaner git diffs because
editing one sentence doesn’t cause cascading line changes throughout a paragraph.

Example transformation:
```
# Before (traditional wrapping)
This is a long paragraph that wraps at 80 columns. When you edit
the first sentence, the entire paragraph reflows and shows as
changed in git diff.

# After (semantic line breaks)
This is a long paragraph that uses semantic line breaks.
When you edit the first sentence, only that line changes in git diff.
The rest of the paragraph stays exactly the same.
```

## Smart Typography

With `--smartquotes` and `--ellipses`:
- `"straight quotes"` → `"curly quotes"`
- `'apostrophes'` → `'apostrophes'`
- `...` → `…`

## Notes

- Flowmark preserves Markdown structure (headers, code blocks, lists)
- Code blocks and inline code are never modified
- Works with stdin/stdout for pipeline integration
- Creates `.orig` backup files with `--inplace` (use `--nobackup` to disable)
- Settings come from explicit flags first, then a config file (`.flowmark.toml`,
  `flowmark.toml`, or `[tool.flowmark]` in `pyproject.toml`), then the `--auto` preset,
  then the built-in defaults; every on/off flag has a `--no-` form

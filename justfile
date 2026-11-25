ai_review_ci_schema_version := "1"
ai_review_ci_profile := "python"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"

[doc("List available recipes.")]
default:
    @just --list

[doc("Run the commit-tier QC gate through global ai-review-ci (invoked by pre-commit).")]
test-commit:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-commit

[doc("Run the push-tier QC gate: global suite plus flowmark's golden CLI tests.")]
test-push:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-push
    @make test-golden

[doc("Run the CI acceptance gate: global CI QC plus flowmark's golden CLI tests.")]
test-ci:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-ci
    @make test-golden

[doc("Run the full local suite (pytest + golden tryscript tests) for the dev inner loop.")]
test:
    @make test

[doc("Run only the Python unit tests (fast inner loop).")]
test-unit:
    uv run pytest

[doc("Run the golden CLI tests.")]
test-golden:
    @make test-golden

[doc("Auto-format docs/markdown with flowmark.")]
format:
    @make format

[doc("Build the distribution.")]
build:
    @make build

[doc("The release tag and the tarball's SHA-256 change together; see SUPPLY-CHAIN-SECURITY.md.")]
install-pandoc-flowmark bindir=(home_directory() / ".local/bin"):
    #!/usr/bin/env bash
    set -euo pipefail
    tag=flowmark-3.10.2-7
    dir="$(mktemp -d)"
    curl -sSfL -o "$dir/pandoc-flowmark.tar.gz" "https://github.com/dzackgarza/pandoc/releases/download/$tag/$tag-linux-amd64.tar.gz"
    echo "6ff42ab2f99bc59b31c1ae149a9df2aa50a0e41b01252f3f86c4c50fabda75ce  $dir/pandoc-flowmark.tar.gz" | sha256sum -c -
    tar xzf "$dir/pandoc-flowmark.tar.gz" -C "$dir"
    mkdir -p "{{bindir}}"
    install -m755 "$dir/$tag-linux-amd64/pandoc-flowmark" "{{bindir}}/pandoc-flowmark"
    pandoc-flowmark --version | head -1

[doc("Run the scheduled whole-tree audit (semgrep and debt scans) through the central implementation.")]
ambient:
    @just -f ~/ai-review-ci/justfiles/python.just -d . ambient

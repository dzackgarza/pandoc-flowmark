# flowmark dev recipes.
#
# The QC tiers (test-commit / test-push / test-ci) delegate to global ai-review-ci
# (~/ai-review-ci/justfiles/python.just); the global pre-commit and pre-push hooks
# invoke them. flowmark's golden CLI tests are domain-specific and are not part of
# global QC, so they are composed on top of the push/CI tiers. The remaining
# user-facing recipes route to the Makefile, which stays the single source of truth
# for the underlying commands (see development.md). GitHub Actions call uv directly.

# ai-review-ci contract variables consumed by doctor and workflow installers.
ai_review_ci_schema_version := "1"
ai_review_ci_profile := "python"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"

# List available recipes.
default:
    @just --list

# Run the commit-tier QC gate through global ai-review-ci (invoked by pre-commit).
test-commit:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-commit

# Run the push-tier QC gate: global suite plus flowmark's golden CLI tests.
test-push:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-push
    @make test-golden

# Run the CI acceptance gate: global CI QC plus flowmark's golden CLI tests.
test-ci:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-ci
    @make test-golden

# Run the full local suite (pytest + golden tryscript tests) for the dev inner loop.
test:
    @make test

# Run only the Python unit tests (fast inner loop).
test-unit:
    uv run pytest

# Run the golden CLI tests.
test-golden:
    @make test-golden

# Auto-format docs/markdown with flowmark.
format:
    @make format

# Build the distribution.
build:
    @make build

# The release tag and the tarball's SHA-256 change together; see SUPPLY-CHAIN-SECURITY.md.
# Install the pinned pandoc-flowmark reader into /usr/local/bin (CI runners).
install-pandoc-flowmark:
    #!/usr/bin/env bash
    set -euo pipefail
    tag=flowmark-3.10.2-5
    dir="$(mktemp -d)"
    curl -sSfL -o "$dir/pandoc-flowmark.tar.gz" "https://github.com/dzackgarza/pandoc/releases/download/$tag/$tag-linux-amd64.tar.gz"
    echo "1ad496d0d438723b2d9bd34e79039408f2d4952903bf196d45842b3b9657328c  $dir/pandoc-flowmark.tar.gz" | sha256sum -c -
    tar xzf "$dir/pandoc-flowmark.tar.gz" -C "$dir"
    sudo install -m755 "$dir/$tag-linux-amd64/pandoc-flowmark" /usr/local/bin/pandoc-flowmark
    pandoc-flowmark --version | head -1

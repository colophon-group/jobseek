#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"

default_branch="${DEFAULT_BRANCH:-main}"

# Merges performed with GITHUB_TOKEN do not emit new workflow runs. Company
# auto-merges are data-only, so explicitly hand the merged main revision to
# the production CSV sync instead of relying on sync-data.yml's push trigger.
gh workflow run sync-data.yml --repo "$REPO" --ref "$default_branch"

echo "Dispatched production CSV sync for ${PR:+PR #$PR on }$default_branch"

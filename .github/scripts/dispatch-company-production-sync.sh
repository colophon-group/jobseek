#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"

default_branch="${DEFAULT_BRANCH:-main}"
git check-ref-format --branch "$default_branch" >/dev/null

# Merges performed with GITHUB_TOKEN do not emit new workflow runs. Company
# auto-merges are data-only, so explicitly hand the merged main revision to
# the production sync instead of relying on push path triggers. The sync owns
# the mandatory exact-revision OG prewarm gate before it mutates production.
target_revision=$(gh api "repos/$REPO/commits/$default_branch" --jq .sha)
if [[ ! "$target_revision" =~ ^[a-f0-9]{40}$ ]]; then
  echo "Unable to resolve exact $default_branch revision" >&2
  exit 1
fi

# Publish through the normal CSV sync. The deployed Proxy snapshot deliberately
# does not need to contain
# a brand-new slug: candidates absent from that snapshot take the bounded
# Typesense status path until the next genuine web release regenerates the
# fast bypass matcher. This preserves immediate visibility and hard 404s
# without replacing the Next.js build ID and cold-starting every page cache.
# The sync first attests OG coverage, then invalidates the company CSV tag after
# Typesense is ready.
gh workflow run sync-data.yml \
  --repo "$REPO" \
  --ref "$default_branch" \
  -f revision="$target_revision"

echo "Dispatched production CSV sync for prewarmed revision $target_revision (${PR:+PR #$PR on }$default_branch)"

#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"

default_branch="${DEFAULT_BRANCH:-main}"
git check-ref-format --branch "$default_branch" >/dev/null

# Merges performed with GITHUB_TOKEN do not emit new workflow runs. Company
# auto-merges are data-only, so explicitly hand the merged main revision to
# every required data consumer instead of relying on push path triggers.
dispatch_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run prewarm-company-og-cache.yml \
  --repo "$REPO" \
  --ref "$default_branch" \
  -f concurrency=4

prewarm_run_id=""
prewarm_sha=""
for attempt in $(seq 1 30); do
  prewarm_run=$(gh run list \
    --repo "$REPO" \
    --workflow prewarm-company-og-cache.yml \
    --branch "$default_branch" \
    --event workflow_dispatch \
    --limit 20 \
    --json databaseId,createdAt,headSha \
    --jq ".[] | select(.createdAt >= \"$dispatch_started\") | [.databaseId, .headSha] | @tsv" \
    | head -n1)
  if [[ -n "$prewarm_run" ]]; then
    IFS=$'\t' read -r prewarm_run_id prewarm_sha <<< "$prewarm_run"
    break
  fi
  echo "Waiting for company OG prewarm run to appear (attempt $attempt/30)"
  sleep 2
done

if [[ -z "$prewarm_run_id" || ! "$prewarm_sha" =~ ^[a-f0-9]{40}$ ]]; then
  echo "Unable to identify the dispatched company OG prewarm run" >&2
  exit 1
fi

echo "Waiting for company OG prewarm run $prewarm_run_id"
gh run watch "$prewarm_run_id" --repo "$REPO" --exit-status

# Publish the company through the normal CSV sync only after all versioned OG
# objects and completion markers exist. A failed prewarm keeps the new company
# out of production rather than exposing a broken social image.
gh workflow run sync-data.yml \
  --repo "$REPO" \
  --ref "$default_branch" \
  -f revision="$prewarm_sha"

echo "Dispatched production CSV sync for prewarmed revision $prewarm_sha (${PR:+PR #$PR on }$default_branch)"

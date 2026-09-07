#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${TARGET_REVISION:?TARGET_REVISION is required}"

default_branch="${DEFAULT_BRANCH:-main}"
git check-ref-format --branch "$default_branch" >/dev/null
if [[ ! "$TARGET_REVISION" =~ ^[a-f0-9]{40}$ ]]; then
  echo "TARGET_REVISION must be an exact lowercase Git SHA" >&2
  exit 1
fi

# A unique title binds the waiter to this full-matrix dispatch. Revision-only
# matching could mistake a same-revision manual canary for the required run.
dispatch_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
handoff_token=$(openssl rand -hex 16)
if [[ ! "$handoff_token" =~ ^[a-f0-9]{32}$ ]]; then
  echo "Unable to generate company OG handoff token" >&2
  exit 1
fi

gh workflow run prewarm-company-og-cache.yml \
  --repo "$REPO" \
  --ref "$default_branch" \
  -f concurrency=4 \
  -f target_revision="$TARGET_REVISION" \
  -f handoff_token="$handoff_token"

prewarm_run_id=""
for attempt in $(seq 1 30); do
  prewarm_run=$(gh run list \
    --repo "$REPO" \
    --workflow prewarm-company-og-cache.yml \
    --branch "$default_branch" \
    --event workflow_dispatch \
    --limit 20 \
    --json databaseId,createdAt,displayTitle \
    --jq ".[] | select(.createdAt >= \"$dispatch_started\" and .displayTitle == \"Prewarm company OG $TARGET_REVISION $handoff_token\") | .databaseId" \
    | head -n1)
  if [[ -n "$prewarm_run" ]]; then
    prewarm_run_id="$prewarm_run"
    break
  fi
  echo "Waiting for exact-revision company OG prewarm to appear (attempt $attempt/30)"
  sleep 2
done

if [[ ! "$prewarm_run_id" =~ ^[1-9][0-9]*$ ]]; then
  echo "Unable to identify the dispatched company OG prewarm run" >&2
  exit 1
fi

echo "Waiting for company OG prewarm run $prewarm_run_id"
gh run watch "$prewarm_run_id" --repo "$REPO" --exit-status
echo "Company OG coverage attested for $TARGET_REVISION by run $prewarm_run_id"

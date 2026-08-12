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

# Company routes are compiled out of Proxy from the registry. Bot-authored
# merges do not emit push workflows, so explicitly deploy and wait for the
# exact prewarmed revision before publishing its Typesense data. A failure
# leaves the company unpublished instead of serving it through a stale Proxy
# matcher or exposing a broken social image.
web_deploy_started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run deploy-web-production.yml \
  --repo "$REPO" \
  --ref "$default_branch" \
  -f revision="$prewarm_sha"

web_deploy_run_id=""
for attempt in $(seq 1 30); do
  web_deploy_run_id=$(gh run list \
    --repo "$REPO" \
    --workflow deploy-web-production.yml \
    --branch "$default_branch" \
    --event workflow_dispatch \
    --limit 20 \
    --json databaseId,createdAt,headSha \
    --jq ".[] | select(.createdAt >= \"$web_deploy_started\" and .headSha == \"$prewarm_sha\") | .databaseId" \
    | head -n1)
  if [[ "$web_deploy_run_id" =~ ^[0-9]+$ ]]; then
    break
  fi
  web_deploy_run_id=""
  echo "Waiting for web deployment run to appear (attempt $attempt/30)"
  sleep 2
done

if [[ -z "$web_deploy_run_id" ]]; then
  echo "Unable to identify the dispatched web deployment run for $prewarm_sha" >&2
  exit 1
fi

echo "Waiting for web deployment run $web_deploy_run_id"
gh run watch "$web_deploy_run_id" --repo "$REPO" --exit-status

# Publish through the normal CSV sync only after the matching web deployment
# is live. The sync invalidates the company CSV tag after Typesense is ready.
gh workflow run sync-data.yml \
  --repo "$REPO" \
  --ref "$default_branch" \
  -f revision="$prewarm_sha"

echo "Dispatched production CSV sync for prewarmed revision $prewarm_sha (${PR:+PR #$PR on }$default_branch)"

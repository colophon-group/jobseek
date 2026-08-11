#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${DEFAULT_BRANCH:?DEFAULT_BRANCH is required}"
: "${DEPLOYED_SHA:?DEPLOYED_SHA is required}"
: "${DEPLOYMENT_URL:?DEPLOYMENT_URL is required}"

if [[ ! "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'Invalid REPO: %s\n' "$REPO" >&2
  exit 2
fi
if [[ ! "$DEFAULT_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  printf 'Invalid DEFAULT_BRANCH: %s\n' "$DEFAULT_BRANCH" >&2
  exit 2
fi
if [[ ! "$DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'Invalid DEPLOYED_SHA: %s\n' "$DEPLOYED_SHA" >&2
  exit 2
fi
if [[ ! "$DEPLOYMENT_URL" =~ ^https://[A-Za-z0-9.-]+\.vercel\.app/?$ ]]; then
  printf 'Invalid DEPLOYMENT_URL: %s\n' "$DEPLOYMENT_URL" >&2
  exit 2
fi

main_sha=$(gh api "repos/$REPO/commits/$DEFAULT_BRANCH" --jq .sha)
if [[ ! "$main_sha" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'GitHub returned an invalid main SHA: %s\n' "$main_sha" >&2
  exit 2
fi

if [[ "$DEPLOYED_SHA" == "$main_sha" ]]; then
  printf '{"event":"vercel_production_sha_verified","sha":"%s","deploymentUrl":"%s"}\n' \
    "$DEPLOYED_SHA" "$DEPLOYMENT_URL"
  exit 0
fi

message="Vercel Production deployed $DEPLOYED_SHA, but $DEFAULT_BRANCH is $main_sha"
printf '::error title=Stale Vercel Production deployment::%s. Inspect %s and promote the Ready main deployment only after smoke checks.\n' \
  "$message" "$DEPLOYMENT_URL"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    printf '## Stale Vercel Production deployment\n\n'
    printf -- '- Deployment: `%s`\n' "$DEPLOYMENT_URL"
    printf -- '- Deployed SHA: `%s`\n' "$DEPLOYED_SHA"
    printf -- '- Current `%s` SHA: `%s`\n\n' "$DEFAULT_BRANCH" "$main_sha"
    printf 'Smoke-test the Ready deployment for the current main SHA, then use `vercel promote <deployment>` to restore production.\n'
  } >> "$GITHUB_STEP_SUMMARY"
fi

exit 1

#!/usr/bin/env bash
# Pre-push hook: block pushes to branches whose PR is already merged.
set -euo pipefail

branch=$(git rev-parse --abbrev-ref HEAD)

# Skip for main, HEAD (detached), or branches without a PR
if [[ "$branch" == "main" || "$branch" == "HEAD" ]]; then
  exit 0
fi

pr_state=$(gh pr view "$branch" --json state -q .state 2>/dev/null || true)

if [[ "$pr_state" == "MERGED" ]]; then
  echo "ERROR: PR for branch \"$branch\" is already merged. Do not push to merged PRs." >&2
  exit 1
fi

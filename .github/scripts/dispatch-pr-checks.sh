#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

OWNER="${REPO%%/*}"

pr_json=$(gh pr view "$PR" --repo "$REPO" \
  --json state,isDraft,headRefName,headRepositoryOwner \
  --jq '{state,isDraft,headRefName,headRepositoryOwner}')

state=$(jq -r '.state' <<< "$pr_json")
draft=$(jq -r '.isDraft' <<< "$pr_json")
branch="${BRANCH:-$(jq -r '.headRefName' <<< "$pr_json")}"
head_owner=$(jq -r '.headRepositoryOwner.login' <<< "$pr_json")

if [[ "$state" != "OPEN" ]]; then
  echo "PR #$PR is $state; not dispatching checks"
  exit 0
fi

if [[ "$draft" == "true" ]]; then
  echo "PR #$PR is draft; not dispatching checks"
  exit 0
fi

if [[ "$head_owner" != "$OWNER" ]]; then
  echo "PR #$PR is from $head_owner, not $OWNER; not dispatching checks"
  exit 0
fi

if [[ "$branch" != add-company/* ]]; then
  echo "PR #$PR branch is $branch, not add-company/*; not dispatching checks"
  exit 0
fi

echo "Dispatching path-aware CI for PR #$PR on $branch"
output=$(gh workflow run ci.yml --repo "$REPO" --ref "$branch" -f "pr=$PR" 2>&1) && {
  echo "$output"
  exit 0
}
status=$?
echo "$output"

if grep -q 'Unexpected inputs provided: \["pr"\]' <<< "$output"; then
  echo "::warning::Branch $branch does not yet include path-aware workflow_dispatch input; a later rebase retry will dispatch CI."
  exit 0
fi

exit "$status"

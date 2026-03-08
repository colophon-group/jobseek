#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

issues=$(gh pr view "$PR" --repo "$REPO" --json closingIssuesReferences --jq '.closingIssuesReferences[].number')
if [[ -z "${issues//$'\n'/}" ]]; then
  echo "No linked closing issues for PR #$PR"
  exit 0
fi

for issue in $issues; do
  state=$(gh issue view "$issue" --repo "$REPO" --json state --jq '.state')
  if [[ "$state" != "OPEN" ]]; then
    echo "Issue #$issue already $state"
    continue
  fi

  labels=$(gh issue view "$issue" --repo "$REPO" --json labels --jq '.labels[].name')
  if echo "$labels" | grep -q '^company-request$'; then
    echo "Closing company-request issue #$issue"
    gh issue close "$issue" --repo "$REPO" \
      --comment "Closed via #$PR (auto-merged by GitHub Actions)."
  else
    echo "Skipping issue #$issue (not a company-request)"
  fi
done

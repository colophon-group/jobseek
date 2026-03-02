#!/usr/bin/env bash
#
# Select the oldest open company-request issue that has no active PR.
# If a PR exists but is stale, post a one-time warning comment and select the issue.
#
# Required env vars: GH_TOKEN, REPO, GITHUB_OUTPUT
# Optional env vars: CONFIG_STALE_HOURS (default 24), CODE_STALE_HOURS (default 72)

set -euo pipefail

: "${REPO:?REPO is required}"

CONFIG_STALE_HOURS="${CONFIG_STALE_HOURS:-24}"
CODE_STALE_HOURS="${CODE_STALE_HOURS:-72}"

# List open company-request issues, oldest first
ISSUES=$(gh issue list --repo "$REPO" \
  --label company-request --state open \
  --json number --jq '.[].number' | sort -n)

if [ -z "$ISSUES" ]; then
  echo "No open company-request issues."
  echo "selected=" >> "$GITHUB_OUTPUT"
  exit 0
fi

SELECTED=""
for ISSUE_NUM in $ISSUES; do
  echo "--- Checking issue #$ISSUE_NUM ---"

  # Search for open PRs that close this issue on a relevant branch
  MATCHING_PRS=$(gh pr list --repo "$REPO" --state open \
    --search "Closes #$ISSUE_NUM" \
    --json number,headRefName,labels,body \
    --jq "[.[] | select(
      (.headRefName | startswith(\"add-company/\")) or
      (.headRefName | startswith(\"fix-crawler/\"))
    )]")

  PR_COUNT=$(echo "$MATCHING_PRS" | jq 'length')

  if [ "$PR_COUNT" -eq 0 ]; then
    echo "No open PR for issue #$ISSUE_NUM — selecting it."
    SELECTED="$ISSUE_NUM"
    break
  fi

  # PR exists — check staleness
  PR_NUM=$(echo "$MATCHING_PRS" | jq -r '.[0].number')
  HAS_REVIEW_CODE=$(echo "$MATCHING_PRS" | jq -r \
    '.[0].labels | map(.name) | if index("review-code") then "true" else "false" end')

  if [ "$HAS_REVIEW_CODE" = "true" ]; then
    STALE_HOURS=$CODE_STALE_HOURS
  else
    STALE_HOURS=$CONFIG_STALE_HOURS
  fi

  # Get last commit date on the PR branch
  LAST_COMMIT_DATE=$(gh pr view "$PR_NUM" --repo "$REPO" \
    --json commits --jq '.commits[-1].committedDate')

  if [ -z "$LAST_COMMIT_DATE" ]; then
    echo "Could not determine last commit date for PR #$PR_NUM — skipping issue."
    continue
  fi

  LAST_COMMIT_TS=$(date -d "$LAST_COMMIT_DATE" +%s 2>/dev/null \
    || date -j -f "%Y-%m-%dT%H:%M:%SZ" "$LAST_COMMIT_DATE" +%s 2>/dev/null)
  NOW_TS=$(date +%s)
  AGE_HOURS=$(( (NOW_TS - LAST_COMMIT_TS) / 3600 ))

  echo "PR #$PR_NUM: last commit ${AGE_HOURS}h ago (threshold: ${STALE_HOURS}h)"

  if [ "$AGE_HOURS" -ge "$STALE_HOURS" ]; then
    # Check if we already posted a stale comment
    EXISTING=$(gh pr view "$PR_NUM" --repo "$REPO" \
      --json comments --jq '.comments[].body' \
      | grep -c '<!-- stale-check -->' || true)

    if [ "$EXISTING" -eq 0 ]; then
      PR_TYPE=$([ "$HAS_REVIEW_CODE" = "true" ] && echo "code changes" || echo "config PRs")
      gh pr comment "$PR_NUM" --repo "$REPO" --body "<!-- stale-check -->
:warning: **This PR may be stale**

This PR has been open for **${AGE_HOURS}h** with no recent commits (threshold: ${STALE_HOURS}h for ${PR_TYPE}).

If work is still in progress, push an update. Otherwise, close this PR to unblock the issue for re-processing."
      echo "Posted stale comment on PR #$PR_NUM."
    else
      echo "Stale comment already exists on PR #$PR_NUM."
    fi

    echo "PR #$PR_NUM is stale — selecting issue #$ISSUE_NUM."
    SELECTED="$ISSUE_NUM"
    break
  else
    echo "PR #$PR_NUM is active — skipping issue #$ISSUE_NUM."
  fi
done

echo "selected=$SELECTED" >> "$GITHUB_OUTPUT"

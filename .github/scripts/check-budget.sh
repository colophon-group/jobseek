#!/usr/bin/env bash
#
# Check the 5h rolling-window budget for resolved company requests.
#
# Required env vars: GH_TOKEN, BUDGET_PER_5H, GITHUB_OUTPUT

set -euo pipefail

: "${BUDGET_PER_5H:?BUDGET_PER_5H is required}"

SINCE=$(date -u -d '5 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || date -u -v-5H +%Y-%m-%dT%H:%M:%SZ)

PROCESSED=$(gh run list \
  --workflow resolve-company-requests.yml \
  --status success \
  --created ">$SINCE" \
  --json conclusion -q 'length')

REMAINING=$(( BUDGET_PER_5H - PROCESSED ))

echo "processed=$PROCESSED"  >> "$GITHUB_OUTPUT"
echo "remaining=$REMAINING"  >> "$GITHUB_OUTPUT"
echo "Budget: $PROCESSED/$BUDGET_PER_5H used in last 5h ($REMAINING remaining)"

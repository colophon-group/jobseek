#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

prs_file="$RUNNER_TEMP/company-prs.sorted.txt"
if [[ "${EVENT_NAME:-}" == workflow_dispatch && -n "${INPUT_PR:-}" ]]; then
  [[ "$INPUT_PR" =~ ^[1-9][0-9]*$ ]] || {
    echo "Invalid PR number: $INPUT_PR" >&2
    exit 1
  }
  echo "$INPUT_PR" > "$prs_file"
else
  # Workflow-level concurrency can replace a pending event with another one.
  # Every automatic wake reconciles the queue, including PRs beyond page one.
  prs_json=$(gh api --paginate "repos/$REPO/pulls?state=open&per_page=100")
  jq -e -s 'length > 0 and all(.[]; type == "array")' >/dev/null <<< "$prs_json"
  jq -rs --arg repo "$REPO" --arg base "${DEFAULT_BRANCH:-main}" '
    .[][]
    | select(.draft == false and .head.repo.full_name == $repo and .base.ref == $base)
    | select(.head.ref | startswith("add-company/") or startswith("fix-crawler/"))
    | .number
  ' <<< "$prs_json" | sort -nu > "$prs_file"
fi

count=$(wc -l < "$prs_file" | tr -d ' ')
echo "count=$count" >> "$GITHUB_OUTPUT"
echo "file=$prs_file" >> "$GITHUB_OUTPUT"
cat "$prs_file"

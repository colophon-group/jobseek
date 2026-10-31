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
elif [[ "${EVENT_NAME:-}" == pull_request_target ]]; then
  jq -er '.pull_request.number | select(type == "number" and . > 0 and floor == .)' \
    "$GITHUB_EVENT_PATH" > "$prs_file"
elif [[ "${EVENT_NAME:-}" == workflow_run ]]; then
  jq -er '.workflow_run | type == "object"' "$GITHUB_EVENT_PATH" >/dev/null
  jq -r '.workflow_run.pull_requests[]?.number | select(type == "number" and . > 0 and floor == .)' \
    "$GITHUB_EVENT_PATH" | sort -nu > "$prs_file"
  if [[ ! -s "$prs_file" ]]; then
    # workflow_dispatch CI runs can omit pull_requests. Resolve their exact
    # same-repo branch with one API call; the scheduled sweep covers events
    # replaced by workflow concurrency.
    branch=$(jq -r '.workflow_run.head_branch // ""' "$GITHUB_EVENT_PATH")
    head_repo=$(jq -r '.workflow_run.head_repository.full_name // ""' "$GITHUB_EVENT_PATH")
    if [[ "$head_repo" == "$REPO" && \
          ( "$branch" == add-company/* || "$branch" == fix-crawler/* ) ]]; then
      head_query=$(jq -nr --arg head "${REPO%%/*}:$branch" '$head | @uri')
      prs_json=$(gh api --paginate "repos/$REPO/pulls?state=open&head=$head_query&per_page=100")
      jq -e -s 'length > 0 and all(.[]; type == "array")' >/dev/null <<< "$prs_json"
      jq -rs --arg repo "$REPO" --arg branch "$branch" '
        .[][] | select(.draft == false and .head.repo.full_name == $repo and .head.ref == $branch)
        | .number
      ' <<< "$prs_json" | sort -nu > "$prs_file"
    fi
  fi
else
  # The scheduled (or manual unscoped) sweep recovers pending events replaced
  # by workflow concurrency, including PRs beyond page one.
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

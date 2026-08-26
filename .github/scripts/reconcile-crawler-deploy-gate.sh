#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${TARGET_URL:?TARGET_URL is required}"

prs_json="$(
  gh pr list --repo "$REPO" --state open --limit 1000 \
    --json number
)" || {
  echo "ERROR: could not list open pull requests" >&2
  exit 1
}
jq -e '
  type == "array" and
  all(.[];
    (.number | type == "number"))
' <<<"$prs_json" >/dev/null || {
  echo "ERROR: open pull-request query returned malformed JSON" >&2
  exit 1
}

prs="$(jq -r '.[].number' <<<"$prs_json")"
[[ -n "$prs" ]] || {
  echo "No open pull requests require crawler deployment reconciliation"
  exit 0
}

# Every event reconciles every open PR from current repository state. Combined
# with the workflow's single non-cancelling job concurrency group, this keeps an
# older evaluator from overwriting a newer hold decision and makes a replacement
# pending run converge the entire queue rather than only its triggering PR.
while IFS= read -r pr; do
  [[ "$pr" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: invalid pull-request number in reconciliation input" >&2
    exit 1
  }

  # Re-read state and identity immediately before evaluation. The queue listing
  # can be stale by the time a serialized run reaches a later PR.
  pr_json="$(
    gh pr view "$pr" --repo "$REPO" --json state,isDraft,headRefOid
  )" || {
    echo "ERROR: could not refresh PR #$pr before reconciliation" >&2
    exit 1
  }
  jq -e '
    type == "object" and
    (.state | type == "string") and
    (.isDraft | type == "boolean") and
    (.headRefOid | type == "string")
  ' <<<"$pr_json" >/dev/null || {
    echo "ERROR: PR #$pr refresh returned malformed JSON" >&2
    exit 1
  }
  IFS=$'\t' read -r state_now draft head_sha <<<"$(
    jq -r '[.state, (.isDraft | tostring), .headRefOid] | @tsv' \
      <<<"$pr_json"
  )"
  [[ "$state_now" == "OPEN" ]] || continue
  [[ "$draft" == "true" || "$draft" == "false" ]] || {
    echo "ERROR: PR #$pr has invalid draft state" >&2
    exit 1
  }
  [[ "$head_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "ERROR: PR #$pr has invalid head SHA" >&2
    exit 1
  }

  state=failure
  description="Draft PR; no merge authority is granted"
  if [[ "$draft" == "false" ]]; then
    description="Crawler deployment is held"
    if PR="$pr" DRAFT=false \
      .github/scripts/check-crawler-deploy-gate.sh; then
      state=success
      description="No crawler deployment hold applies"
    fi
  fi

  gh api --method POST "repos/$REPO/statuses/$head_sha" \
    -f state="$state" \
    -f context="Crawler Deploy Gate" \
    -f description="$description" \
    -f target_url="$TARGET_URL" >/dev/null
done <<<"$prs"

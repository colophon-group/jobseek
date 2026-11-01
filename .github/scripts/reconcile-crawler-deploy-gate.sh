#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${TARGET_URL:?TARGET_URL is required}"

prs_json="$(
  gh pr list --repo "$REPO" --state open --limit 1000 \
    --json number,isDraft,headRefOid,statusCheckRollup
)" || {
  echo "ERROR: could not list open pull requests" >&2
  exit 1
}
jq -e '
  type == "array" and
  all(.[];
    (.number | type == "number") and
    (.isDraft | type == "boolean") and
    (.headRefOid | type == "string") and
    (.statusCheckRollup | type == "array"))
' <<<"$prs_json" >/dev/null || {
  echo "ERROR: open pull-request query returned malformed JSON" >&2
  exit 1
}
prs="$(jq -r '.[].number' <<<"$prs_json")"
[[ -n "$prs" ]] || {
  echo "No open pull requests require crawler deployment reconciliation"
  exit 0
}

# With no open deployment hold, every ready PR has the same gate decision.
# Read holds once for the common path; re-read before posting a new success so
# a hold opened during this sweep cannot be ignored by that write.
read_holds() {
  local holds
  holds="$(gh issue list --repo "$REPO" --state open \
    --label 'deployment-hold:crawler' --limit 100 --json number)" || return 1
  jq -e 'type == "array"' <<<"$holds" >/dev/null || return 1
  printf '%s' "$holds"
}

holds_json="$(read_holds)" || {
  echo "ERROR: could not read crawler deployment holds" >&2
  exit 1
}
hold_count="$(jq length <<<"$holds_json")"

# Every event reconciles every open PR. A pending run may be replaced by a
# newer event in GitHub's non-cancelling concurrency group, so even a
# bot-authored dispatch must sweep the queue to preserve hold convergence.
# The single status writer and live hold re-read keep writes current.
while IFS= read -r pr; do
  [[ "$pr" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: invalid pull-request number in reconciliation input" >&2
    exit 1
  }

  # The bulk listing is sufficient to avoid a per-PR refresh when no write is
  # needed: without a hold, an already-successful Gate on the listed current
  # head is the desired state for a ready PR. Any missing, unknown or held
  # context still takes the exact-head refresh and status-write path below.
  if [[ "$hold_count" == 0 ]] && jq -e --argjson pr "$pr" '
    .[] | select(.number == $pr) |
    .isDraft == false and
    ([.statusCheckRollup[]
      | select(.__typename == "StatusContext" and .context == "Crawler Deploy Gate")
      | .state] | last // "" | ascii_downcase) == "success"
  ' <<<"$prs_json" >/dev/null; then
    echo "PR #$pr crawler deployment gate unchanged"
    continue
  fi

  # Re-read state and identity immediately before evaluation. The queue listing
  # can be stale by the time a serialized run reaches a later PR.
  pr_json="$(
    gh pr view "$pr" --repo "$REPO" --json state,isDraft,headRefOid,statusCheckRollup
  )" || {
    echo "ERROR: could not refresh PR #$pr before reconciliation" >&2
    exit 1
  }
  jq -e '
    type == "object" and
    (.state | type == "string") and
    (.isDraft | type == "boolean") and
    (.headRefOid | type == "string") and
    (.statusCheckRollup | type == "array")
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

  current_gate_state="$(jq -r '
    [.statusCheckRollup[]
      | select(.__typename == "StatusContext" and .context == "Crawler Deploy Gate")
      | .state] | last // "" | ascii_downcase
  ' <<<"$pr_json")"

  state=failure
  description="Draft PR; no merge authority is granted"
  if [[ "$draft" == "false" ]]; then
    if [[ "$hold_count" == 0 ]]; then
      # An unchanged success belongs to this exact head SHA. No file query or
      # status API call is needed while holds are absent.
      if [[ "$current_gate_state" == success ]]; then
        echo "PR #$pr crawler deployment gate unchanged"
        continue
      fi
      holds_now="$(read_holds)" || {
        echo "ERROR: could not refresh crawler deployment holds" >&2
        exit 1
      }
    else
      holds_now="$holds_json"
    fi
    if [[ "$(jq length <<<"$holds_now")" == 0 ]] || \
      PR="$pr" DRAFT=false .github/scripts/check-crawler-deploy-gate.sh; then
      state=success
      description="No crawler deployment hold applies"
    else
      description="Crawler deployment is held"
    fi
  fi

  # This workflow rechecks every open PR on every event. GitHub caps the
  # number of statuses per SHA/context, so a long-lived PR can exhaust its
  # allowance even when its gate decision never changed. The combined-status
  # endpoint returns the latest status for each context.
  if [[ "$state" == success && "$current_gate_state" == success ]]; then
    echo "PR #$pr crawler deployment gate unchanged"
    continue
  fi

  status_json="$(gh api "repos/$REPO/commits/$head_sha/status")" || {
    echo "ERROR: could not read current gate status for PR #$pr" >&2
    exit 1
  }
  jq -e '
    type == "object" and (.statuses | type == "array")
  ' <<<"$status_json" >/dev/null || {
    echo "ERROR: malformed current gate status for PR #$pr" >&2
    exit 1
  }
  if jq -e --arg state "$state" --arg description "$description" '
    any(.statuses[];
      .context == "Crawler Deploy Gate" and
      .state == $state and
      .description == $description)
  ' <<<"$status_json" >/dev/null; then
    echo "PR #$pr crawler deployment gate unchanged"
    continue
  fi

  # A queued evaluator must not publish to a head that was replaced while it
  # inspected holds and statuses. A later PR or trusted dispatch run will
  # evaluate the new snapshot. Recheck holds again immediately before a new
  # success; a hold may have opened while the status API was being read.
  final_pr_json="$(gh pr view "$pr" --repo "$REPO" \
    --json state,isDraft,headRefOid)" || {
    echo "ERROR: could not refresh PR #$pr before status write" >&2
    exit 1
  }
  if ! jq -e --arg sha "$head_sha" --argjson draft "$draft" '
    .state == "OPEN" and .isDraft == $draft and .headRefOid == $sha
  ' <<<"$final_pr_json" >/dev/null; then
    echo "PR #$pr changed before gate status write; retry on the next event"
    continue
  fi
  if [[ "$state" == success ]]; then
    final_holds="$(read_holds)" || {
      echo "ERROR: could not refresh crawler deployment holds before status write" >&2
      exit 1
    }
    if [[ "$(jq length <<<"$final_holds")" != 0 ]] && \
       ! PR="$pr" DRAFT=false .github/scripts/check-crawler-deploy-gate.sh; then
      state=failure
      description="Crawler deployment is held"
    fi
  fi

  gh api --method POST "repos/$REPO/statuses/$head_sha" \
    -f state="$state" \
    -f context="Crawler Deploy Gate" \
    -f description="$description" \
    -f target_url="$TARGET_URL" >/dev/null
done <<<"$prs"

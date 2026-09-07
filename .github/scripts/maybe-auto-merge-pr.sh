#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

SCRIPTS_DIR="${TRUSTED_SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
OWNER="${REPO%%/*}"

pr_json=$(gh pr view "$PR" --repo "$REPO" \
  --json state,isDraft,headRefName,headRepositoryOwner \
  --jq '{state,isDraft,headRefName,headRepositoryOwner}')

state=$(jq -r '.state' <<< "$pr_json")
draft=$(jq -r '.isDraft' <<< "$pr_json")
branch=$(jq -r '.headRefName' <<< "$pr_json")
head_owner=$(jq -r '.headRepositoryOwner.login' <<< "$pr_json")

if [[ "$state" != "OPEN" ]]; then
  echo "PR #$PR is $state; skipping"
  exit 0
fi

if [[ "$draft" == "true" ]]; then
  echo "PR #$PR is draft; skipping"
  exit 0
fi

if [[ "$head_owner" != "$OWNER" ]]; then
  echo "PR #$PR is from $head_owner, not $OWNER; skipping"
  exit 0
fi

if [[ "$branch" != add-company/* ]]; then
  echo "PR #$PR branch is $branch, not add-company/*; skipping"
  exit 0
fi

CLASSIFIED_LABELS=""
CLASSIFIED_BASE_SHA=""
CLASSIFIED_CRAWL_STATS_FINGERPRINT=""
CLASSIFIED_HEAD_SHA=""
BLOCKING_LABELS=(
  "review-code"
  "review-size"
  "review-load"
  "incomplete"
  "on hold"
  "needs-independent-review"
  "gate:human-ui"
)

classify_current_head() {
  local label_output
  label_output=$(mktemp)
  GITHUB_OUTPUT="$label_output" "$SCRIPTS_DIR/label-pr.sh"
  CLASSIFIED_LABELS=$(grep '^labels=' "$label_output" | tail -1 | cut -d= -f2- || true)
  CLASSIFIED_BASE_SHA=$(grep '^base_sha=' "$label_output" | tail -1 | cut -d= -f2- || true)
  CLASSIFIED_CRAWL_STATS_FINGERPRINT=$(grep '^crawl_stats_fingerprint=' "$label_output" | tail -1 | cut -d= -f2- || true)
  CLASSIFIED_HEAD_SHA=$(grep '^head_sha=' "$label_output" | tail -1 | cut -d= -f2- || true)
  rm -f "$label_output"

  if [[ ! "$CLASSIFIED_HEAD_SHA" =~ ^[0-9a-f]{40}$ || ! "$CLASSIFIED_BASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "PR #$PR labeler did not return valid head/base SHAs" >&2
    exit 1
  fi
  if [[ ! "$CLASSIFIED_CRAWL_STATS_FINGERPRINT" =~ ^[0-9a-f]{64}$ ]]; then
    echo "PR #$PR labeler did not return a valid crawl-stats fingerprint" >&2
    exit 1
  fi
}

has_label() {
  local labels="$1"
  local wanted="$2"
  while IFS= read -r label; do
    [[ "$label" == "$wanted" ]] && return 0
  done < <(tr ',' '\n' <<< "$labels")
  return 1
}

labels_are_merge_eligible() {
  local labels="$1"
  has_label "$labels" "auto-merge" || return 1
  for label in "${BLOCKING_LABELS[@]}"; do
    has_label "$labels" "$label" && return 1
  done
  return 0
}

classification_is_currently_eligible() {
  if ! labels_are_merge_eligible "$CLASSIFIED_LABELS"; then
    echo "PR #$PR classified labels are '$CLASSIFIED_LABELS'; not auto-merging"
    return 1
  fi

  local current_labels
  if ! current_labels=$(gh pr view "$PR" --repo "$REPO" --json labels --jq '.labels[].name'); then
    echo "PR #$PR labels could not be refreshed" >&2
    return 1
  fi
  if ! labels_are_merge_eligible "$current_labels"; then
    echo "PR #$PR current labels block auto-merge"
    return 1
  fi
}

classify_current_head

if ! classification_is_currently_eligible; then
  exit 0
fi

files=$(gh api --paginate "repos/$REPO/pulls/$PR/files" --jq '.[].filename')
if grep -q '^apps/crawler/data/images/' <<< "$files"; then
  echo "PR #$PR has pending image files; upload-company-images will handle it"
  exit 0
fi

required_ci_state() {
  gh pr view "$PR" --repo "$REPO" --json statusCheckRollup --jq '
    [
      .statusCheckRollup[]
      | select(
          (.__typename == "StatusContext" and .context == "Required CI")
          or (.__typename == "CheckRun" and .name == "Required CI")
        )
      | if .__typename == "StatusContext" then .state else .conclusion end
    ]
    | last // ""
  '
}

wait_for_required_ci() {
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24; do
    state=$(required_ci_state)

    case "$state" in
      SUCCESS|success)
        echo "PR #$PR Required CI is successful"
        return 0
        ;;
      FAILURE|ERROR|CANCELLED|TIMED_OUT|ACTION_REQUIRED|failure|cancelled|timed_out|action_required)
        echo "PR #$PR Required CI is $state; not auto-merging"
        return 1
        ;;
      "")
        echo "PR #$PR is waiting for Required CI to be reported (attempt $attempt/24)"
        ;;
      *)
        echo "PR #$PR Required CI is $state; waiting (attempt $attempt/24)"
        ;;
    esac

    sleep 10
  done

  echo "PR #$PR Required CI did not become successful in time"
  return 1
}

rollup_state() {
  local lease_json="$1"
  local check_name="$2"
  jq -r --arg check_name "$check_name" '
    [
      .statusCheckRollup[]
      | select(
          (.__typename == "StatusContext" and .context == $check_name)
          or (.__typename == "CheckRun" and .name == $check_name)
        )
      | if .__typename == "StatusContext" then .state else .conclusion end
    ]
    | last // ""
  ' <<< "$lease_json"
}

acquire_merge_lease() {
  local expected_head="$1"
  local expected_base="$2"
  local expected_crawl_stats_fingerprint="$3"

  # Refresh and bind evidence before taking the final PR snapshot. The
  # combined PR query below must remain the last remote read so an operator
  # blocker added while comments are fetched cannot be missed.
  local comments crawl_stats_evidence crawl_stats_found crawl_stats_fingerprint
  if ! comments=$(gh api --method GET --paginate --slurp \
    "repos/$REPO/issues/$PR/comments?per_page=100"); then
    echo "PR #$PR crawl-stats evidence could not be refreshed" >&2
    return 1
  fi
  if ! crawl_stats_evidence=$(python3 "$SCRIPTS_DIR/crawl-stats-evidence.py" \
    "$expected_head" <<< "$comments"); then
    echo "PR #$PR crawl-stats evidence was malformed" >&2
    return 1
  fi
  crawl_stats_found=$(jq -er '.found | booleans' <<< "$crawl_stats_evidence") || return 1
  crawl_stats_fingerprint=$(jq -er '.fingerprint | strings' <<< "$crawl_stats_evidence") || return 1
  if [[ "$crawl_stats_found" != "true" \
    || ! "$crawl_stats_fingerprint" =~ ^[0-9a-f]{64}$ \
    || "$crawl_stats_fingerprint" != "$expected_crawl_stats_fingerprint" ]]; then
    echo "PR #$PR crawl-stats evidence changed before merge"
    return 1
  fi

  local lease_json
  if ! lease_json=$(gh pr view "$PR" --repo "$REPO" \
    --json state,isDraft,headRefOid,baseRefOid,labels,statusCheckRollup,mergeable,mergeStateStatus); then
    echo "PR #$PR merge lease could not be read" >&2
    return 1
  fi
  if ! jq -e '
    (.state | type == "string")
    and (.isDraft | type == "boolean")
    and (.headRefOid | type == "string")
    and (.baseRefOid | type == "string")
    and (.labels | type == "array")
    and (.statusCheckRollup | type == "array")
    and (.mergeable | type == "string")
    and (.mergeStateStatus | type == "string")
  ' >/dev/null <<< "$lease_json"; then
    echo "PR #$PR merge lease was malformed" >&2
    return 1
  fi

  local lease_state lease_draft lease_head lease_base lease_labels
  local lease_mergeable lease_merge_state required_state deploy_state
  lease_state=$(jq -r '.state' <<< "$lease_json")
  lease_draft=$(jq -r '.isDraft' <<< "$lease_json")
  lease_head=$(jq -r '.headRefOid' <<< "$lease_json")
  lease_base=$(jq -r '.baseRefOid' <<< "$lease_json")
  lease_labels=$(jq -r '.labels[].name' <<< "$lease_json")
  lease_mergeable=$(jq -r '.mergeable' <<< "$lease_json")
  lease_merge_state=$(jq -r '.mergeStateStatus' <<< "$lease_json")
  required_state=$(rollup_state "$lease_json" "Required CI")
  deploy_state=$(rollup_state "$lease_json" "Crawler Deploy Gate")

  if [[ "$lease_state" != "OPEN" || "$lease_draft" != "false" ]]; then
    echo "PR #$PR is no longer open and ready"
    return 1
  fi
  if [[ ! "$lease_head" =~ ^[0-9a-f]{40}$ || ! "$lease_base" =~ ^[0-9a-f]{40}$ ]]; then
    echo "PR #$PR merge lease has invalid head/base SHAs" >&2
    return 1
  fi
  if [[ "$lease_head" != "$expected_head" || "$lease_base" != "$expected_base" ]]; then
    echo "PR #$PR head/base changed before merge"
    return 1
  fi

  if ! labels_are_merge_eligible "$lease_labels"; then
    echo "PR #$PR labels no longer grant merge authority"
    return 1
  fi
  if [[ "$required_state" != "SUCCESS" && "$required_state" != "success" ]]; then
    echo "PR #$PR Required CI lease is '$required_state'"
    return 1
  fi
  if [[ "$deploy_state" != "SUCCESS" && "$deploy_state" != "success" ]]; then
    echo "PR #$PR Crawler Deploy Gate lease is '$deploy_state'"
    return 1
  fi
  if [[ "$lease_mergeable" != "MERGEABLE" ]]; then
    echo "PR #$PR mergeability is '$lease_mergeable'"
    return 1
  fi
  case "$lease_merge_state" in
    CLEAN|HAS_HOOKS) ;;
    *)
      echo "PR #$PR merge state is '$lease_merge_state'"
      return 1
      ;;
  esac
}

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git fetch origin main "refs/heads/$branch:refs/remotes/origin/$branch"
git checkout -B "$branch" "origin/$branch"

if git merge-base --is-ancestor origin/main HEAD 2>/dev/null; then
  echo "PR #$PR is already up to date"
else
  if git rebase origin/main 2>/dev/null; then
    echo "PR #$PR rebased cleanly"
  else
    while true; do
      conflicted=$(git diff --name-only --diff-filter=U)
      [[ -z "$conflicted" ]] && break

      for file in $conflicted; do
        if [[ "$file" == "apps/crawler/VERSION" ]]; then
          git checkout --ours "$file"
          current=$(tr -d '[:space:]' < "$file")
          IFS='.' read -r major minor patch <<< "$current"
          echo "${major}.${minor}.$((patch + 1))" > "$file"
        elif [[ "$file" == *.csv ]]; then
          git checkout --ours "$file"
          python3 "$SCRIPTS_DIR/merge_company_csv_rebase.py" "$file" REBASE_HEAD
        else
          echo "Unexpected conflict in $file; refusing to discard the PR's changes" >&2
          exit 1
        fi
        git add "$file"
      done

      if GIT_EDITOR=true git rebase --continue 2>/tmp/maybe-auto-merge-rebase.err; then
        if [[ -d .git/rebase-merge || -d .git/rebase-apply ]]; then
          continue
        fi
        break
      fi

      if [[ -z "$(git diff --name-only --diff-filter=U)" ]]; then
        cat /tmp/maybe-auto-merge-rebase.err
        git rebase --skip
        if [[ ! -d .git/rebase-merge && ! -d .git/rebase-apply ]]; then
          break
        fi
      fi
    done
  fi

  pushed_head=$(git rev-parse HEAD)
  git push --force-with-lease origin "$branch"
  classify_current_head
  if [[ "$CLASSIFIED_HEAD_SHA" != "$pushed_head" ]]; then
    echo "PR #$PR classified $CLASSIFIED_HEAD_SHA after pushing $pushed_head" >&2
    exit 1
  fi
  if ! classification_is_currently_eligible; then
    exit 0
  fi
  "$SCRIPTS_DIR/dispatch-pr-checks.sh"
  echo "PR #$PR branch updated; dispatched checks before merge"
fi

AWAITED_HEAD_SHA="$CLASSIFIED_HEAD_SHA"
AWAITED_BASE_SHA="$CLASSIFIED_BASE_SHA"
if ! wait_for_required_ci; then
  echo "PR #$PR is not mergeable yet; scheduled/workflow_run retries will revisit it"
  exit 0
fi

classify_current_head
if [[ "$CLASSIFIED_HEAD_SHA" != "$AWAITED_HEAD_SHA" || "$CLASSIFIED_BASE_SHA" != "$AWAITED_BASE_SHA" ]]; then
  echo "PR #$PR head/base changed while checks were awaited; retrying later"
  exit 0
fi
if ! classification_is_currently_eligible; then
  exit 0
fi

for attempt in 1 2 3; do
  if ! acquire_merge_lease "$CLASSIFIED_HEAD_SHA" "$CLASSIFIED_BASE_SHA" \
    "$CLASSIFIED_CRAWL_STATS_FINGERPRINT"; then
    echo "PR #$PR merge lease is no longer valid; retrying later"
    exit 0
  fi
  if gh pr merge "$PR" --repo "$REPO" --rebase \
    --match-head-commit "$CLASSIFIED_HEAD_SHA" 2>/tmp/maybe-auto-merge.err; then
    echo "PR #$PR merged"
    "$SCRIPTS_DIR/dispatch-company-production-sync.sh"
    "$SCRIPTS_DIR/close-linked-company-request-issues.sh"
    exit 0
  fi

  echo "PR #$PR merge attempt $attempt did not complete yet:"
  cat /tmp/maybe-auto-merge.err
  sleep 10
done

echo "PR #$PR is not mergeable yet; scheduled/workflow_run retries will revisit it"

#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

SCRIPTS_DIR="${TRUSTED_SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
OWNER="${REPO%%/*}"

pr_json=$(gh pr view "$PR" --repo "$REPO" \
  --json state,isDraft,headRefName,headRepositoryOwner,headRefOid,baseRefOid,baseRefName,labels,reviewDecision,mergeStateStatus)

EXPECTED_HEAD=$(jq -r '.headRefOid' <<< "$pr_json")
EXPECTED_BASE=$(jq -r '.baseRefOid' <<< "$pr_json")
export EXPECTED_HEAD EXPECTED_BASE

eligible_snapshot() {
  jq -e --arg owner "$OWNER" --arg head "$EXPECTED_HEAD" --arg base "$EXPECTED_BASE" '
    .state == "OPEN" and .isDraft == false and .baseRefName == "main"
    and .reviewDecision != "CHANGES_REQUESTED"
    and .headRepositoryOwner.login == $owner
    and (.headRefName | startswith("add-company/"))
    and .headRefOid == $head and .baseRefOid == $base
    and ([.labels[]?.name | select(test("^(hold|do-not-merge|no-merge|blocked|deployment-hold)(:|$)"))] | length == 0)
  ' >/dev/null
}

read_snapshot() {
  gh pr view "$PR" --repo "$REPO" \
    --json state,isDraft,headRefName,headRepositoryOwner,headRefOid,baseRefOid,baseRefName,labels,reviewDecision,mergeStateStatus
}

if ! eligible_snapshot <<< "$pr_json"; then
  echo "PR #$PR is not eligible for automated merge"
  exit 0
fi

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

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git fetch origin main "refs/heads/$branch:refs/remotes/origin/$branch"
if [[ "$(git rev-parse "origin/$branch")" != "$EXPECTED_HEAD" ||
      "$(git rev-parse origin/main)" != "$EXPECTED_BASE" ]]; then
  echo "PR or base changed before checkout; retry on the next run"
  exit 0
fi
git checkout -B "$branch" "origin/$branch"

# Refuse to rewrite a branch outside the company auto-merger's authority.
label_output=$(mktemp)
GITHUB_OUTPUT="$label_output" "$SCRIPTS_DIR/label-pr.sh"
labels=$(sed -n 's/^labels=//p' "$label_output" | tail -1)
rm -f "$label_output"
if [[ "$labels" != "auto-merge" ]]; then
  echo "PR #$PR requires separate merge authorization ($labels)"
  exit 0
fi

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

  if ! read_snapshot | eligible_snapshot; then
    echo "PR changed during rebase; stopping"
    exit 0
  fi
  git push --force-with-lease="refs/heads/$branch:$EXPECTED_HEAD" origin "$branch"
  EXPECTED_HEAD=$(git rev-parse HEAD)
  "$SCRIPTS_DIR/dispatch-pr-checks.sh"
  echo "PR #$PR branch updated; dispatched checks before merge"
fi

if ! wait_for_required_ci; then
  echo "PR #$PR is not mergeable yet; scheduled/workflow_run retries will revisit it"
  exit 0
fi

for attempt in 1 2 3; do
  snapshot=$(read_snapshot)
  if ! eligible_snapshot <<< "$snapshot"; then
    echo "PR or base changed; merge authority expired"
    exit 0
  fi
  # Classification reads the immutable fetched OIDs, including after our rebase.
  label_output=$(mktemp)
  GITHUB_OUTPUT="$label_output" "$SCRIPTS_DIR/label-pr.sh"
  labels=$(sed -n 's/^labels=//p' "$label_output" | tail -1)
  rm -f "$label_output"
  if [[ "$labels" != "auto-merge" ]]; then
    echo "PR #$PR labels are '$labels'; not auto-merging"
    exit 0
  fi
  if ! gh pr checks "$PR" --repo "$REPO" --required --json name,state \
      --jq 'length > 0 and all(.[]; .state == "SUCCESS")' | grep -qx true; then
    echo "Required checks are not all successful"
    exit 0
  fi
  final_snapshot=$(read_snapshot)
  if ! eligible_snapshot <<< "$final_snapshot"; then
    echo "PR changed during final validation; stopping"
    exit 0
  fi
  if ! jq -e '.mergeStateStatus == "CLEAN" or .mergeStateStatus == "HAS_HOOKS"' \
      >/dev/null <<< "$final_snapshot"; then
    echo "PR merge state is not clean; stopping"
    exit 0
  fi
  if gh pr merge "$PR" --repo "$REPO" --rebase --match-head-commit "$EXPECTED_HEAD" 2>/tmp/maybe-auto-merge.err; then
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

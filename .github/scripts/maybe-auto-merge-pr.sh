#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

SCRIPTS_DIR="${TRUSTED_SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
OWNER="${REPO%%/*}"

read_snapshot() {
  local snapshot base_tip
  snapshot=$(gh pr view "$PR" --repo "$REPO" \
    --json state,isDraft,headRefName,headRepositoryOwner,headRefOid,baseRefOid,baseRefName,labels,reviewDecision,mergeStateStatus)
  # GitHub can retain the PR's old baseRefOid after main advances. Resolve the
  # actual target ref on every lease read so stale PRs can reach the rebase.
  base_tip=$(gh api "repos/$REPO/git/ref/heads/main" --jq .object.sha)
  [[ "$base_tip" =~ ^[a-f0-9]{40}$ ]] || return 1
  jq --arg base_tip "$base_tip" '. + {baseTipOid: $base_tip}' <<< "$snapshot"
}

pr_json=$(read_snapshot)
EXPECTED_HEAD=$(jq -r '.headRefOid' <<< "$pr_json")
EXPECTED_BASE=$(jq -r '.baseTipOid' <<< "$pr_json")
export EXPECTED_HEAD EXPECTED_BASE

eligible_snapshot() {
  jq -e --arg owner "$OWNER" --arg head "$EXPECTED_HEAD" --arg base "$EXPECTED_BASE" '
    .state == "OPEN" and .isDraft == false and .baseRefName == "main"
    and .reviewDecision != "CHANGES_REQUESTED"
    and .headRepositoryOwner.login == $owner
    and (.headRefName | startswith("add-company/") or startswith("fix-crawler/"))
    and .headRefOid == $head and .baseTipOid == $base
    and ([.labels[]?.name | select(test("^(hold|do-not-merge|no-merge|blocked|deployment-hold)(:|$)"))] | length == 0)
  ' >/dev/null
}

if ! eligible_snapshot <<< "$pr_json"; then
  echo "PR #$PR is not eligible for automated merge"
  exit 0
fi

branch=$(jq -r '.headRefName' <<< "$pr_json")

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

required_ci_succeeded() {
  local state
  state=$(required_ci_state)
  if [[ "$state" == SUCCESS || "$state" == success ]]; then
    echo "PR #$PR Required CI is successful"
    return 0
  fi
  echo "PR #$PR Required CI is ${state:-not reported}; retry after check completion"
  return 1
}

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git fetch origin "+refs/heads/main:refs/remotes/origin/main" \
  "+refs/heads/$branch:refs/remotes/origin/$branch"
if [[ "$(git rev-parse "origin/$branch")" != "$EXPECTED_HEAD" ||
      "$(git rev-parse origin/main)" != "$EXPECTED_BASE" ]]; then
  echo "PR or base changed before checkout; retry on the next run"
  exit 0
fi
# A conflicting PR must leave the checkout usable for the rest of the sweep.
trap 'git rebase --abort >/dev/null 2>&1 || true' EXIT
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

      census_conflicted=false

      for file in $conflicted; do
        if [[ "$file" == "apps/crawler/VERSION" ]]; then
          git checkout --ours "$file"
          current=$(tr -d '[:space:]' < "$file")
          IFS='.' read -r major minor patch <<< "$current"
          echo "${major}.${minor}.$((patch + 1))" > "$file"
        elif [[ "$file" == *.csv ]]; then
          git checkout --ours "$file"
          python3 "$SCRIPTS_DIR/merge_company_csv_rebase.py" "$file" REBASE_HEAD
        elif [[ "$file" == "apps/crawler/tests/lightpanda/fixtures/census.json" ]]; then
          census_conflicted=true
          continue
        else
          echo "Unexpected conflict in $file; refusing to discard the PR's changes" >&2
          exit 1
        fi
        git add "$file"
      done

      if "$census_conflicted"; then
        # Rebuild from the combined boards after semantic CSV merges. Taking
        # either conflicted JSON side would lose another company's records.
        (cd apps/crawler && uv run --frozen python -m src.lightpanda.census \
          --output tests/lightpanda/fixtures/census.json)
        git add apps/crawler/tests/lightpanda/fixtures/census.json
      fi

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

if ! required_ci_succeeded; then
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
  if ! jq -e '
      [.labels[]?.name] as $labels
      | ($labels | index("auto-merge")) != null
        and ($labels | any(. == "review-load" or . == "review-size" or . == "review-code" or . == "incomplete") | not)
    ' >/dev/null <<< "$final_snapshot"; then
    echo "PR decision labels changed during final validation; stopping"
    exit 0
  fi
  # UNSTABLE also includes skipped/advisory checks. Required checks above are
  # authoritative; GitHub still enforces its rules on the expected-head merge.
  if ! jq -e '.mergeStateStatus == "CLEAN" or .mergeStateStatus == "HAS_HOOKS" or .mergeStateStatus == "UNSTABLE"' \
      >/dev/null <<< "$final_snapshot"; then
    echo "PR merge state is $(jq -r .mergeStateStatus <<< "$final_snapshot"); stopping"
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

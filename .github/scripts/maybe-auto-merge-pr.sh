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

files=$(gh api --paginate "repos/$REPO/pulls/$PR/files" --jq '.[].filename')
if grep -q '^apps/crawler/data/images/' <<< "$files"; then
  echo "PR #$PR has pending image files; upload-company-images will handle it"
  exit 0
fi

label_output=$(mktemp)
GITHUB_OUTPUT="$label_output" "$SCRIPTS_DIR/label-pr.sh"
labels=$(grep '^labels=' "$label_output" | tail -1 | cut -d= -f2- || true)
rm -f "$label_output"

if [[ ",$labels," != *",auto-merge,"* ]]; then
  echo "PR #$PR labels are '$labels'; not auto-merging"
  exit 0
fi

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
          git show REBASE_HEAD:"$file" | while IFS= read -r line; do
            if ! grep -qxF "$line" "$file"; then
              echo "$line" >> "$file"
            fi
          done
        else
          echo "::warning::Unexpected conflict in $file; keeping base version"
          git checkout --ours "$file"
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

  git push --force-with-lease origin "$branch"
  echo "PR #$PR branch updated; checks will run before merge"
fi

for attempt in 1 2 3; do
  if gh pr merge "$PR" --repo "$REPO" --rebase 2>/tmp/maybe-auto-merge.err; then
    echo "PR #$PR merged"
    "$SCRIPTS_DIR/close-linked-company-request-issues.sh"
    exit 0
  fi

  echo "PR #$PR merge attempt $attempt did not complete yet:"
  cat /tmp/maybe-auto-merge.err
  sleep 10
done

echo "PR #$PR is not mergeable yet; scheduled/workflow_run retries will revisit it"

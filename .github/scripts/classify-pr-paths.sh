#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

is_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

is_non_code_path() {
  local file="$1"

  case "$file" in
    apps/crawler/data/industries.csv | \
      apps/crawler/data/occupations.csv | \
      apps/crawler/data/seniority.csv | \
      apps/crawler/data/technologies.csv | \
      scripts/derive-crawler-runtime-contract.mjs | \
      scripts/verify-crawler-release-bridge.py)
      return 1
      ;;
  esac

  case "$file" in
    *.md | \
      docs/* | \
      .github/dependabot.yml | \
      .github/dependabot.yaml | \
      .github/ISSUE_TEMPLATE/* | \
      .github/DISCUSSION_TEMPLATE/* | \
      apps/crawler/data/* | \
      apps/crawler/traces/* | \
      apps/crawler/VERSION)
      return 0
      ;;
  esac

  return 1
}

is_crawler_code_path() {
  local file="$1"

  case "$file" in
    apps/crawler/data/industries.csv | \
      apps/crawler/data/occupations.csv | \
      apps/crawler/data/seniority.csv | \
      apps/crawler/data/technologies.csv | \
      scripts/derive-crawler-runtime-contract.mjs | \
      scripts/verify-crawler-release-bridge.py)
      return 0
      ;;
  esac

  [[ "$file" == apps/crawler/* ]] || return 1

  case "$file" in
    *.md | \
      apps/crawler/data/* | \
      apps/crawler/traces/* | \
      apps/crawler/VERSION)
      return 1
      ;;
  esac

  return 0
}

code=false
crawler_code=false
boards_csv=false

while IFS= read -r file; do
  [[ -n "$file" ]] || continue

  if ! is_non_code_path "$file"; then
    code=true
  fi

  if is_crawler_code_path "$file"; then
    crawler_code=true
  fi

  if [[ "$file" == "apps/crawler/data/boards.csv" ]]; then
    boards_csv=true
  fi
done < <(gh api --paginate "repos/$REPO/pulls/$PR/files" --jq '.[].filename')

emit() {
  local name="$1"
  local value="$2"

  echo "$name=$value"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "$name=$value" >> "$GITHUB_OUTPUT"
  fi
}

pr=$(gh api "repos/$REPO/pulls/$PR")
base_ref=$(jq -r '.base.ref' <<<"$pr")
base_sha=$(jq -r '.base.sha' <<<"$pr")
head_sha=$(jq -r '.head.sha' <<<"$pr")
if [[ -z "$base_ref" || "$base_ref" == "null" ]]; then
  echo "PR #$PR has no base branch" >&2
  exit 1
fi
if ! is_sha "$base_sha" || ! is_sha "$head_sha"; then
  echo "PR #$PR did not resolve to exact base and head revisions" >&2
  exit 1
fi
if ! is_sha "$GITHUB_SHA" || [[ "$GITHUB_SHA" != "$head_sha" ]]; then
  echo "manually dispatched revision does not match the current PR head" >&2
  exit 1
fi
if [[ "$(git rev-parse HEAD)" != "$head_sha" ]]; then
  echo "checked-out revision does not match the current PR head" >&2
  exit 1
fi

emit "code" "$code"
emit "crawler_code" "$crawler_code"
emit "boards_csv" "$boards_csv"
emit "codeql" "$code"
emit "is_pr" "true"
emit "base_ref" "$base_ref"
emit "base_sha" "$base_sha"
emit "head_sha" "$head_sha"

#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

is_non_code_path() {
  local file="$1"

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

  [[ "$file" == apps/crawler/* ]] || return 1

  case "$file" in
    *.md | apps/crawler/data/* | apps/crawler/traces/* | apps/crawler/VERSION)
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

emit "code" "$code"
emit "crawler_code" "$crawler_code"
emit "boards_csv" "$boards_csv"
emit "codeql" "$code"

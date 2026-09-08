#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"

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
      apps/crawler/tests/lightpanda/fixtures/census.json | \
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
      apps/crawler/tests/lightpanda/fixtures/census.json | \
      apps/crawler/VERSION)
      return 1
      ;;
  esac

  return 0
}

is_go_http_pilot_path() {
  local file="$1"

  [[ "$file" == pilots/go-http-sitemap/* ]]
}

code=false
crawler_code=false
go_http_pilot=false
boards_csv=false

if ! changed_files=$(gh api --paginate "repos/$REPO/pulls/$PR/files" --jq '.[] | .filename, (.previous_filename // empty)'); then
  echo "Failed to list changed files for PR #$PR" >&2
  exit 1
fi
if [[ -z "$changed_files" ]]; then
  echo "PR #$PR has no changed files" >&2
  exit 1
fi

while IFS= read -r file; do
  [[ -n "$file" ]] || continue

  if ! is_non_code_path "$file"; then
    code=true
  fi

  if is_crawler_code_path "$file"; then
    crawler_code=true
  fi

  if is_go_http_pilot_path "$file"; then
    go_http_pilot=true
  fi

  if [[ "$file" == "apps/crawler/data/boards.csv" ]]; then
    boards_csv=true
  fi
done <<< "$changed_files"

emit() {
  local name="$1"
  local value="$2"

  echo "$name=$value"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "$name=$value" >> "$GITHUB_OUTPUT"
  fi
}

base_ref=$(gh api "repos/$REPO/pulls/$PR" --jq '.base.ref')
if [[ -z "$base_ref" || "$base_ref" == "null" ]]; then
  echo "PR #$PR has no base branch" >&2
  exit 1
fi

emit "code" "$code"
emit "crawler_code" "$crawler_code"
emit "go_http_pilot" "$go_http_pilot"
emit "boards_csv" "$boards_csv"
emit "codeql" "$code"
emit "is_pr" "true"
emit "base_ref" "$base_ref"

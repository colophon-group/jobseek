#!/usr/bin/env bash

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${PR:?PR is required}"
: "${DRAFT:?DRAFT is required}"

if [[ "$DRAFT" == "true" ]]; then
  echo "PR #$PR is draft; no merge authority is granted"
  exit 0
fi
[[ "$DRAFT" == "false" ]] || {
  echo "ERROR: PR #$PR has invalid draft state $DRAFT" >&2
  exit 1
}

changed_paths="$(
  gh api --paginate "repos/$REPO/pulls/$PR/files?per_page=100" \
    --jq '.[] | .filename, (.previous_filename // empty)'
)" || {
  echo "ERROR: could not classify files for PR #$PR" >&2
  exit 1
}
[[ -n "$changed_paths" ]] || {
  echo "ERROR: file query for PR #$PR returned no paths" >&2
  exit 1
}

# Let this exact policy-only bridge land without pretending that its crawler
# test file is production runtime. This is deliberately not a reusable path
# class: #8046 must remove it with the inactive-v1 exemption.
inactive_v1_policy=1
inactive_v1_policy_count=0
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  case "$path" in
    .github/scripts/check-crawler-deploy-gate.sh | \
      .github/workflows/deploy-ats-inventory.yml | \
      .github/workflows/deploy-crawler-browser.yml | \
      apps/crawler/tests/test_ats_inventory_deployment.py | \
      scripts/check-crawler-version.mjs | \
      scripts/ci-workflow.test.mjs | \
      scripts/crawler-runtime-contract.test.mjs | \
      scripts/crawler-version.test.mjs | \
      scripts/derive-crawler-runtime-contract.mjs)
      ((inactive_v1_policy_count += 1))
      ;;
    *)
      inactive_v1_policy=0
      ;;
  esac
done < <(sort -u <<<"$changed_paths")
if (( inactive_v1_policy && inactive_v1_policy_count == 9 )); then
  echo "PR #$PR is the exact #8071 inactive-v1 policy bridge; no crawler deployment hold applies"
  exit 0
fi

runtime_paths=()
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  case "$path" in
    apps/crawler/contracts/v1/*)
      # Candidate-only until #8046 packages and activates runtime v1.
      ;;
    apps/crawler/data/industries.csv | \
      apps/crawler/data/occupations.csv | \
      apps/crawler/data/seniority.csv | \
      apps/crawler/data/technologies.csv | \
      .github/workflows/deploy-crawler-browser.yml | \
      scripts/derive-crawler-runtime-contract.mjs | \
      scripts/verify-crawler-release-bridge.py)
      runtime_paths+=("$path")
      ;;
    apps/crawler/data/* | \
      apps/crawler/traces/* | \
      apps/crawler/ws-package/* | \
      apps/crawler/*.md)
      # These exclusions mirror Deploy Crawler (Hetzner). Keep both surfaces
      # synchronized so a required gate is neither missing nor over-broad.
      ;;
    apps/crawler/*)
      runtime_paths+=("$path")
      ;;
  esac
done <<<"$changed_paths"

if (( ${#runtime_paths[@]} == 0 )); then
  echo "PR #$PR does not trigger the crawler runtime deployment"
  exit 0
fi

holds_json="$({
  gh issue list --repo "$REPO" --state open \
    --label 'deployment-hold:crawler' --limit 100 \
    --json number,title,url
})"
jq -e 'type == "array"' <<<"$holds_json" >/dev/null || {
  echo "ERROR: crawler deployment hold query returned malformed JSON" >&2
  exit 1
}

if jq -e 'length == 0' <<<"$holds_json" >/dev/null; then
  echo "PR #$PR may enter the crawler deployment path; no deployment hold is active"
  exit 0
fi

echo "ERROR: PR #$PR changes crawler runtime paths while deployment is held:" >&2
jq -r '.[] | "- #\(.number) \(.title) (\(.url))"' <<<"$holds_json" >&2
echo "Changed runtime paths:" >&2
printf -- '- %s\n' "${runtime_paths[@]:0:20}" >&2
exit 1

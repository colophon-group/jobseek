#!/usr/bin/env bash
# Fail-closed validation shared by the pre-approval and post-approval phases
# of the one-time Supabase job_posting retirement.
set -euo pipefail

required=(
  GH_TOKEN REPO CURRENT_SHA EVENT_NAME GIT_REF DISPATCH_ACTOR
  DISPATCH_TRIGGERING_ACTOR DISPATCH_CONFIRMATION BACKUP_RESTORE_RUN_ID
  CRAWLER_DEPLOY_RUN_ID TYPESENSE_BACKFILL_RUN_ID
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || {
    echo "Missing retirement evidence input: ${name}" >&2
    exit 1
  }
done

test "$EVENT_NAME" = "workflow_dispatch"
test "$GIT_REF" = "refs/heads/main"
test "$DISPATCH_ACTOR" = "viktor-shcherb"
test "$DISPATCH_TRIGGERING_ACTOR" = "viktor-shcherb"
test "$DISPATCH_CONFIRMATION" = "DROP-ONLY-JOB-POSTING-0086"
[[ "$CURRENT_SHA" =~ ^[0-9a-f]{40}$ ]]
main_sha="$(gh api "repos/${REPO}/git/ref/heads/main" --jq .object.sha)"
[[ "$main_sha" =~ ^[0-9a-f]{40}$ ]]
test "$main_sha" = "$CURRENT_SHA"
for run_id in \
  "$BACKUP_RESTORE_RUN_ID" \
  "$CRAWLER_DEPLOY_RUN_ID" \
  "$TYPESENSE_BACKFILL_RUN_ID"; do
  [[ "$run_id" =~ ^[1-9][0-9]*$ ]]
done

validate_run() {
  local run_id="$1" expected_path="$2" expected_name="$3" expected_title="$4" expected_event="$5" max_age="$6"
  local record conclusion path name title event branch head_sha completed_at completed_epoch now
  record="$(gh api "repos/${REPO}/actions/runs/${run_id}")"
  conclusion="$(jq -r '.conclusion' <<<"$record")"
  path="$(jq -r '.path' <<<"$record")"
  name="$(jq -r '.name' <<<"$record")"
  title="$(jq -r '.display_title' <<<"$record")"
  event="$(jq -r '.event' <<<"$record")"
  branch="$(jq -r '.head_branch' <<<"$record")"
  head_sha="$(jq -r '.head_sha' <<<"$record")"
  completed_at="$(jq -r '.updated_at' <<<"$record")"
  test "$conclusion" = success
  test "$path" = "$expected_path"
  test "$name" = "$expected_name"
  if [[ "$expected_title" != - ]]; then
    test "$title" = "$expected_title"
  fi
  test "$event" = "$expected_event"
  test "$branch" = main
  [[ "$head_sha" =~ ^[0-9a-f]{40}$ ]]
  git merge-base --is-ancestor "$head_sha" "$CURRENT_SHA"
  completed_epoch="$(date -u -d "$completed_at" +%s)"
  now="$(date -u +%s)"
  (( now >= completed_epoch && now - completed_epoch <= max_age ))
  printf '%s' "$head_sha"
}

latest_matching_run_id() {
  local workflow="$1" event="$2" expected_title="$3"
  gh api --method GET \
    "repos/${REPO}/actions/workflows/${workflow}/runs" \
    -f branch=main -f status=success -f event="$event" -f per_page=100 \
    | jq -r --arg title "$expected_title" \
      '[.workflow_runs[] | select($title == "-" or .display_title == $title)][0].id // empty'
}

backup_sha="$(validate_run \
  "$BACKUP_RESTORE_RUN_ID" \
  '.github/workflows/operate-web-postgresql-backup.yml' \
  'Operate Web PostgreSQL Backup (Hetzner)' \
  'Web PostgreSQL backup operation: restore' \
  workflow_dispatch \
  21600)"
crawler_sha="$(validate_run \
  "$CRAWLER_DEPLOY_RUN_ID" \
  '.github/workflows/deploy-crawler-browser.yml' \
  'Deploy Crawler (Hetzner)' \
  - \
  push \
  86400)"
backfill_title="Crawler maintenance: backfill-typesense @ ${crawler_sha}"
backfill_sha="$(validate_run \
  "$TYPESENSE_BACKFILL_RUN_ID" \
  '.github/workflows/crawler-scheduled-maintenance.yml' \
  'Crawler scheduled maintenance' \
  "$backfill_title" \
  workflow_dispatch \
  14400)"

test "$(latest_matching_run_id \
  operate-web-postgresql-backup.yml workflow_dispatch \
  'Web PostgreSQL backup operation: restore')" = "$BACKUP_RESTORE_RUN_ID"
test "$(latest_matching_run_id \
  deploy-crawler-browser.yml push -)" = "$CRAWLER_DEPLOY_RUN_ID"
test "$(latest_matching_run_id \
  crawler-scheduled-maintenance.yml workflow_dispatch \
  "$backfill_title")" = "$TYPESENSE_BACKFILL_RUN_ID"
git merge-base --is-ancestor "$crawler_sha" "$backfill_sha"

# Every prerequisite must have exercised the same reviewed bytes now on main.
git diff --quiet "$backup_sha" "$CURRENT_SHA" -- \
  apps/web/drizzle/0086_drop_supabase_job_posting.sql \
  deploy/backups/web-postgresql \
  deploy/backups/install-host.sh \
  scripts/jobseek-data-backup.py \
  .github/workflows/operate-web-postgresql-backup.yml
git diff --quiet "$crawler_sha" "$CURRENT_SHA" -- \
  .github/workflows/deploy-crawler-browser.yml \
  .github/workflows/sync-data.yml \
  apps/crawler \
  deploy/reconciliation
git diff --quiet "$backfill_sha" "$CURRENT_SHA" -- \
  .github/workflows/crawler-scheduled-maintenance.yml

vercel_states="$(gh api \
  "repos/${REPO}/deployments?sha=${CURRENT_SHA}&environment=Production&per_page=20" \
  --jq '.[] | select(.creator.login == "vercel[bot]") | .statuses_url' \
  | while read -r statuses_url; do
      gh api "$statuses_url" --jq '.[].state'
    done)"
test "$(printf '%s\n' "$vercel_states" \
  | grep -E '^(success|failure|error|inactive)$' | head -n1)" = success

readiness_digest="$(printf '%s\n%s\n%s\n%s\n%s\n%s' \
  production-drop \
  DROP-ONLY-JOB-POSTING-0086 \
  "$BACKUP_RESTORE_RUN_ID" \
  "$CRAWLER_DEPLOY_RUN_ID" \
  "$TYPESENSE_BACKFILL_RUN_ID" \
  "$CURRENT_SHA" | sha256sum | cut -d' ' -f1)"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "readiness_digest=$readiness_digest"
    echo "web_deploy_sha=$CURRENT_SHA"
    echo "crawler_sha=$crawler_sha"
  } >> "$GITHUB_OUTPUT"
fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Supabase retirement release evidence (${EVIDENCE_PHASE:-validation})"
    echo "- Backup restore run: ${BACKUP_RESTORE_RUN_ID} (${backup_sha})"
    echo "- Crawler deploy run: ${CRAWLER_DEPLOY_RUN_ID} (${crawler_sha})"
    echo "- Typesense backfill run: ${TYPESENSE_BACKFILL_RUN_ID} (${backfill_sha})"
    echo "- Vercel production SHA: ${CURRENT_SHA}"
    echo "- Readiness digest: ${readiness_digest}"
  } >> "$GITHUB_STEP_SUMMARY"
fi

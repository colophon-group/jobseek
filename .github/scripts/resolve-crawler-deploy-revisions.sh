#!/usr/bin/env bash
set -euo pipefail

: "${DEFAULT_BRANCH:?DEFAULT_BRANCH is required}"
: "${EVENT_NAME:?EVENT_NAME is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${GITHUB_REF:?GITHUB_REF is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

git check-ref-format "refs/heads/$DEFAULT_BRANCH" >/dev/null
test "$GITHUB_REF" = "refs/heads/$DEFAULT_BRANCH"
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git rev-parse --verify HEAD)" = "$GITHUB_SHA"

# A manual run can be launched against any ref. Re-fetch and attest the
# default branch immediately before releasing so a branch SHA or stale main
# checkout cannot reach the production environment.
git fetch --no-tags origin \
  "+refs/heads/${DEFAULT_BRANCH}:refs/remotes/origin/${DEFAULT_BRANCH}"
test "$(git rev-parse --verify "origin/${DEFAULT_BRANCH}^{commit}")" = "$GITHUB_SHA"

case "$EVENT_NAME" in
  push)
    previous_revision="${EVENT_BEFORE:-}"
    manual_redeploy=false
    ;;
  workflow_dispatch)
    # workflow_dispatch has no event.before. The exact main first parent is
    # the rollback/runtime comparison revision for a secret-only rollout.
    previous_revision="$(git rev-parse --verify "${GITHUB_SHA}^")"
    manual_redeploy=true
    ;;
  *)
    echo "Unsupported deployment event: $EVENT_NAME" >&2
    exit 1
    ;;
esac

[[ "$previous_revision" =~ ^[0-9a-f]{40}$ ]]
test "$previous_revision" != "0000000000000000000000000000000000000000"
test "$(git rev-parse --verify "${previous_revision}^{commit}")" = "$previous_revision"
git merge-base --is-ancestor "$previous_revision" "$GITHUB_SHA"
printf 'previous_revision=%s\n' "$previous_revision" >>"$GITHUB_OUTPUT"
printf 'manual_redeploy=%s\n' "$manual_redeploy" >>"$GITHUB_OUTPUT"

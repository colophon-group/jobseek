#!/usr/bin/env bash
# Shared helpers for the production crawler deploy.

stop_crawler_services() {
  local timeout="$1" listing id service extra requested
  shift
  local -a ids=()
  [[ "$timeout" =~ ^[1-9][0-9]*$ && $# -gt 0 &&
     "${COMPOSE_PROJECT_NAME:-}" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || {
    echo "ERROR: crawler quiescence scope is invalid" >&2
    return 1
  }
  for requested in "$@"; do
    case "$requested" in
      worker-1|worker-2|worker-3|browser-1|ordinary-go|lease-reaper|exporter|drain|lightpanda-claimant) ;;
      *) echo "ERROR: crawler quiescence service is invalid" >&2; return 1 ;;
    esac
  done
  # Removed services can still be running from the previous authenticated
  # Compose generation. Discover by project/service labels, not the incoming
  # spec, then stop the complete requested writer set in one bounded call.
  listing="$(docker ps --no-trunc --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
    --format '{{.ID}} {{.Label "com.docker.compose.service"}}')" || return $?
  while read -r id service extra; do
    [[ -n "$id" ]] || continue
    [[ "$id" =~ ^[a-f0-9]{64}$ && -n "$service" && -z "$extra" ]] || {
      echo "ERROR: crawler quiescence container discovery is invalid" >&2
      return 1
    }
    for requested in "$@"; do
      if [[ "$service" == "$requested" ]]; then
        ids+=("$id")
        break
      fi
    done
  done <<<"$listing"
  (( ${#ids[@]} == 0 )) || docker stop --timeout "$timeout" "${ids[@]}"
}

pull_compose_service_with_retry() {
  local service="$1"
  local attempts="${DEPLOY_PULL_ATTEMPTS:-3}"
  local retry_delay="${DEPLOY_PULL_RETRY_DELAY_SECONDS:-2}"
  local attempt

  if [[ ! "$attempts" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: DEPLOY_PULL_ATTEMPTS must be a positive integer" >&2
    return 2
  fi
  if [[ ! "$retry_delay" =~ ^[0-9]+$ ]]; then
    echo "ERROR: DEPLOY_PULL_RETRY_DELAY_SECONDS must be a non-negative integer" >&2
    return 2
  fi

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    echo "Pulling image for Compose service ${service} (attempt ${attempt}/${attempts})" >&2
    if docker compose pull "$service"; then
      return 0
    fi

    if (( attempt == attempts )); then
      echo "ERROR: image pull failed for ${service} after ${attempts} attempts" >&2
      return 1
    fi

    echo "Image pull failed for ${service}; retrying in ${retry_delay}s" >&2
    sleep "$retry_delay"
  done
}

pull_deploy_images() {
  local service

  # All native processors and maintenance use the same immutable image.
  # Pull one service per image, sequentially, to avoid shared-layer races.
  local services=(ordinary-go redis alloy)
  for service in "${services[@]}"; do
    pull_compose_service_with_retry "$service" || return $?
  done
}

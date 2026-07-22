#!/usr/bin/env bash
# Shared helpers for the production crawler deploy.

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

  # Pull one service per unique image, sequentially. The slim and browser
  # crawler images share layers; pulling the whole Compose project in parallel
  # can race inside containerd while both downloads commit the same blob.
  # The remaining services that use the slim image reuse worker-1's local tag.
  local services=(worker-1 browser-1 redis alloy murmur-shim)
  for service in "${services[@]}"; do
    pull_compose_service_with_retry "$service" || return $?
  done
}

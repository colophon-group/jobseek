#!/usr/bin/env bash
# Sourced by the selected deployment; immutable binaries arrive with its specs.
initialize_native_deploy_tools() {
  local architecture directory expected name
  local expected_revision="${2:-${JOBSEEK_DEPLOY_REVISION:-}}"
  case "$(uname -m)" in
    x86_64) architecture=amd64 ;;
    aarch64 | arm64) architecture=arm64 ;;
    *) echo "ERROR: native deployment tool architecture is unsupported" >&2; return 1 ;;
  esac
  directory="${1:-$INCOMING_DIR/scripts}"
  for name in crawler-deploy-tools.source crawler-deploy-tools.sha256 crawler-deploy-tools-amd64 crawler-deploy-tools-arm64; do
    [[ -f "$directory/$name" && ! -L "$directory/$name" ]] || {
      echo "ERROR: native deployment artifact is unavailable or unsafe" >&2; return 1;
    }
  done
  expected="$(cat "$directory/crawler-deploy-tools.source")"
  [[ "$expected" =~ ^[0-9a-f]{40}$ && "$expected" == "$expected_revision" ]] || {
    echo "ERROR: native deployment tool source differs" >&2; return 1;
  }
  # Only the two reviewed artifact names may be verified from this manifest.
  [[ "$(wc -l <"$directory/crawler-deploy-tools.sha256")" -eq 2 ]] && \
    LC_ALL=C grep -Eq '^[0-9a-f]{64}  crawler-deploy-tools-amd64$' "$directory/crawler-deploy-tools.sha256" && \
    LC_ALL=C grep -Eq '^[0-9a-f]{64}  crawler-deploy-tools-arm64$' "$directory/crawler-deploy-tools.sha256" || {
      echo "ERROR: native deployment artifact manifest differs" >&2; return 1;
    }
  (cd "$directory" && sha256sum --strict --status -c crawler-deploy-tools.sha256) || {
    echo "ERROR: native deployment artifact digest differs" >&2; return 1;
  }
  NATIVE_DEPLOY_TOOLS="$directory/crawler-deploy-tools-$architecture"
  [[ "$(env -i "$NATIVE_DEPLOY_TOOLS" --build-info)" == "{\"source_revision\":\"$expected\"}" ]] || {
    echo "ERROR: compiled deployment source differs" >&2; return 1;
  }
}

run_native_postgresql_preflight() {
  env -i "GRAFANA_PROM_URL=$GRAFANA_PROM_URL" \
    "GRAFANA_PROM_USERNAME=$GRAFANA_PROM_USERNAME" \
    "GRAFANA_PROM_PASSWORD=$GRAFANA_PROM_PASSWORD" \
    "$NATIVE_DEPLOY_TOOLS" postgresql-preflight
}

run_native_host_hygiene() {
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin "$NATIVE_DEPLOY_TOOLS" host-hygiene
}

# The digest-pinned image advertises its supported recovery CLI. Never infer
# support from the failed incoming release or fall back after a command fails.
crawler_image_maintenance_backend() {
  local image="$1" capability
  [[ "$image" =~ ^ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: maintenance image must be immutable" >&2; return 1;
  }
  capability="$(docker image inspect --format '{{index .Config.Labels "org.jobseek.crawler.native-maintenance"}}' "$image")" || {
    echo "ERROR: maintenance image capability is unavailable" >&2; return 1;
  }
  case "$capability" in
    v1) printf '%s\n' native ;;
    ''|'<no value>') printf '%s\n' legacy ;;
    *) echo "ERROR: maintenance image capability is unsupported" >&2; return 1 ;;
  esac
}

#!/usr/bin/env bash
# Sourced by the selected deployment; immutable binaries arrive with its specs.
initialize_native_deploy_tools() {
  local architecture directory expected name
  case "$(uname -m)" in
    x86_64) architecture=amd64 ;;
    aarch64 | arm64) architecture=arm64 ;;
    *) echo "ERROR: native deployment tool architecture is unsupported" >&2; return 1 ;;
  esac
  directory="$INCOMING_DIR/scripts"
  for name in crawler-deploy-tools.source crawler-deploy-tools.sha256 crawler-deploy-tools-amd64 crawler-deploy-tools-arm64; do
    [[ -f "$directory/$name" && ! -L "$directory/$name" ]] || {
      echo "ERROR: native deployment artifact is unavailable or unsafe" >&2; return 1;
    }
  done
  expected="$(cat "$directory/crawler-deploy-tools.source")"
  [[ "$expected" =~ ^[0-9a-f]{40}$ && "$expected" == "$JOBSEEK_DEPLOY_REVISION" ]] || {
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

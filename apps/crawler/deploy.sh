#!/usr/bin/env bash
# Deploy crawler containers on Hetzner (worker machine).
# Postgres runs on a separate dedicated machine.
# Called by CI with env vars set from GitHub secrets.
set -euo pipefail

# Serialize deploys with host-scheduled data maintenance. The database-level
# reconciler lock prevents duplicate jobs, while this host lock also closes
# the race where a new timer starts after deploy preflight but before the old
# writer containers are quiesced.
exec 9>/run/lock/jobseek-crawler-mutation.lock
flock -w 7200 9 || {
  echo "ERROR: timed out waiting for the crawler mutation lock" >&2
  exit 1
}

# ── Validate required env vars ─────────────────────────────────────────
required_vars=(
  OWNER
  GHCR_PULL_USERNAME
  GHCR_PULL_TOKEN
  CRAWLER_IMAGE_TAG
  CRAWLER_IMAGE_REF
  BROWSER_IMAGE_REF
  JOBSEEK_DEPLOY_REVISION
  JOBSEEK_RUNTIME_CONTRACT_SHA256
  JOBSEEK_DATA_CONTRACT_SHA256
  JOBSEEK_PREVIOUS_DATA_REVISION
  JOBSEEK_PREVIOUS_RUNTIME_CONTRACT_SHA256
  JOBSEEK_PREVIOUS_DATA_CONTRACT_SHA256
  JOBSEEK_PREVIOUS_DATA_CANDIDATE_ID
  JOBSEEK_PREVIOUS_DATA_ARCHIVE_SHA256
  JOBSEEK_RECONCILIATION_WRAPPER_SHA256
  WEB_DATABASE_URL
  LOCAL_DATABASE_URL
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_ENDPOINT_URL
  R2_DOMAIN_URL
  R2_BUCKET
  GRAFANA_PROM_URL
  GRAFANA_PROM_USERNAME
  GRAFANA_PROM_PASSWORD
  GRAFANA_LOKI_URL
  GRAFANA_LOKI_USERNAME
  GRAFANA_LOKI_PASSWORD
  TYPESENSE_HOST
  TYPESENSE_PORT
  TYPESENSE_PROTOCOL
  TYPESENSE_OPERATIONS_KEY
  # Murmur shim secret. Without this, the shim's compose env
  # substitution `${MURMUR_TOKEN}` resolves to empty on a full-stack
  # redeploy and the shim accepts every request as anonymous. The
  # H4 deploy workflow (deploy-murmur-shim.yml) persists this in its
  # transactionally published environment and active release generation;
  # the full-stack deploy must carry the same protected value forward.
  # Required since H3 (#2775) added the murmur-shim service.
  MURMUR_TOKEN
)

missing=()
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing required env vars: ${missing[*]}" >&2
  exit 1
fi

DEPLOY_DIR="/home/deploy"
INCOMING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_VERIFIER="$INCOMING_DIR/scripts/verify-crawler-release-bridge.py"
ENV_FILE="$DEPLOY_DIR/.env"
ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"
ROLLBACK_SPEC_ARCHIVE="$DEPLOY_DIR/.deploy-spec.rollback.tar"
ROLLBACK_POOL_OVERRIDE="$DEPLOY_DIR/.crawler-rollback-pool-budget.override.yml"
ROLLBACK_POOL_OVERRIDE_SOURCE="$INCOMING_DIR/rollback-pool-budget.override.yml"
ACTIVE_RELEASE_ROOT="$DEPLOY_DIR/.crawler-release-generations"
ACTIVE_RELEASE_POINTER="$DEPLOY_DIR/.crawler-active-release"
LEGACY_DEPLOY_SUCCESS_FILE="$DEPLOY_DIR/.crawler-deploy-success.env"
ACTIVE_RELEASE_DIR=""
ACTIVE_COMPOSE_SNAPSHOT=""
ACTIVE_COMPOSE_SNAPSHOT_SHA256=""
ACTIVE_ENV_SNAPSHOT=""
ACTIVE_ENV_SNAPSHOT_SHA256=""
DEPLOY_SUCCESS_FILE=""
ACTIVE_RELEASE_MANIFEST=""
ACTIVE_RELEASE_FORMAT=""
ACTIVE_IMAGE_OVERRIDE=""
ACTIVE_DATA_SNAPSHOT=""
ACTIVE_DATA_FILES_MANIFEST=""
ROLLBACK_ACTIVE_RELEASE_TARGET=""
ROLLBACK_ACTIVE_IMAGE_OVERRIDE=""
ROLLBACK_SYNC_WEB_DATABASE_URL=""
FORWARD_SYNC_STARTED=0
FORWARD_DATA_STAGING_ROOT=""
FORWARD_DATA_SNAPSHOT=""
FORWARD_DATA_FILES_MANIFEST=""
GHCR_DOCKER_CONFIG=""
ENV_FILE_WAS_PRESENT=0
ROLLBACK_ARMED=0
ROLLBACK_RUNNING=0
DEPLOY_SPEC_FILES=(
  deploy.sh
  deploy_helpers.sh
  docker-compose.yml
  alloy.river
  scripts/postgresql-operational-preflight.py
  scripts/verify-crawler-release-bridge.py
)
if [[ "$INCOMING_DIR" == "$DEPLOY_DIR" ]]; then
  echo "ERROR: deploy artifacts must be staged outside the active deploy directory" >&2
  exit 1
fi
for spec in "${DEPLOY_SPEC_FILES[@]}"; do
  [[ -f "$INCOMING_DIR/$spec" ]] || {
    echo "ERROR: staged deploy artifact is unavailable: ${spec}" >&2
    exit 1
  }
done
[[ -f "$INCOMING_DIR/scripts/crawler-csv-sync-host.sh" && \
  ! -L "$INCOMING_DIR/scripts/crawler-csv-sync-host.sh" ]] || {
  echo "ERROR: staged CSV publication helper is unavailable or unsafe" >&2
  exit 1
}
[[ -f "$ROLLBACK_POOL_OVERRIDE_SOURCE" && ! -L "$ROLLBACK_POOL_OVERRIDE_SOURCE" ]] || {
  echo "ERROR: staged rollback pool-budget override is unavailable or unsafe" >&2
  exit 1
}
# Staged path is intentionally dynamic; the workflow verifies and supplies it.
# shellcheck disable=SC1091
source "$INCOMING_DIR/deploy_helpers.sh"
IMAGE_TAG="$CRAWLER_IMAGE_TAG"
REDIS_IMAGE="redis:8-alpine@sha256:978f0e01593e65eed801f2402944efcd936d43b5027e4908a7897baf88ed6241"
SHIM_IMAGE_REF="${SHIM_IMAGE_REF:-}"
DEPLOY_MIN_FREE_KB="${DEPLOY_MIN_FREE_KB:-5242880}" # 5 GiB hard floor.
DEPLOY_PRUNE_FREE_KB="${DEPLOY_PRUNE_FREE_KB:-10485760}" # Prune cache below 10 GiB.
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$DEPLOY_DIR")}"
export COMPOSE_PROJECT_NAME
MAINTENANCE_OPERATION=crawler-deploy
MAINTENANCE_ISSUE=3409
MAINTENANCE_BUDGET_SECONDS=1800
MAINTENANCE_MARKER_NAME=""
if [[ ! "$JOBSEEK_DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: JOBSEEK_DEPLOY_REVISION must be a full lowercase Git commit SHA" >&2
  exit 1
fi
if [[ ! "$JOBSEEK_RUNTIME_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: JOBSEEK_RUNTIME_CONTRACT_SHA256 must be a lowercase SHA-256" >&2
  exit 1
fi
if [[ ! "$JOBSEEK_DATA_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ ||
  ! "$JOBSEEK_PREVIOUS_RUNTIME_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ ||
  ! "$JOBSEEK_PREVIOUS_DATA_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ ||
  ! "$JOBSEEK_PREVIOUS_DATA_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ||
  ! "$JOBSEEK_PREVIOUS_DATA_REVISION" =~ ^[0-9a-f]{40}$ ||
  ! "$JOBSEEK_PREVIOUS_DATA_CANDIDATE_ID" =~ ^${JOBSEEK_PREVIOUS_DATA_REVISION}-[1-9][0-9]*-[1-9][0-9]*$
]]; then
  echo "ERROR: crawler data publication identity is invalid" >&2
  exit 1
fi
if [[ ! "$OWNER" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "ERROR: OWNER must be a lowercase GitHub owner" >&2
  exit 1
fi
if [[ ! "$IMAGE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-build\.[1-9][0-9]*\.g[0-9a-f]{7,12})?$ ]]; then
  echo "ERROR: CRAWLER_IMAGE_TAG must be a versioned release/build tag" >&2
  exit 1
fi
if [[ ! "$CRAWLER_IMAGE_REF" =~ ^ghcr\.io/${OWNER}/jobseek-crawler@sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: CRAWLER_IMAGE_REF must be an immutable crawler digest" >&2
  exit 1
fi
if [[ ! "$BROWSER_IMAGE_REF" =~ ^ghcr\.io/${OWNER}/jobseek-crawler-browser@sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: BROWSER_IMAGE_REF must be an immutable crawler-browser digest" >&2
  exit 1
fi
if [[ ! "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: JOBSEEK_RECONCILIATION_WRAPPER_SHA256 must be a lowercase SHA-256" >&2
  exit 1
fi
MAINTENANCE_PROVENANCE_LABELS=(
  --label "com.docker.compose.project=${COMPOSE_PROJECT_NAME}"
  --label com.docker.compose.container-number=1
  --label com.docker.compose.oneoff=True
  --label "jobseek.maintenance.operation=${MAINTENANCE_OPERATION}"
  --label "jobseek.maintenance.issue=${MAINTENANCE_ISSUE}"
  --label "jobseek.maintenance.revision=${JOBSEEK_DEPLOY_REVISION}"
  --label "jobseek.maintenance.budget-seconds=${MAINTENANCE_BUDGET_SECONDS}"
)
ALLOY_IMAGE="grafana/alloy:v1.18.1@sha256:0f4434c92b3e6cdac38bb129b344e1790c246f7b6e2eaffcc16a5fa363240e33"
ALLOY_STATE_ACTIVATION_REQUIRED=0

cleanup_ghcr_docker_config() {
  local config_dir="${GHCR_DOCKER_CONFIG:-}"

  if [[ -z "$config_dir" ]]; then
    unset DOCKER_CONFIG
    return 0
  fi
  if [[ "$config_dir" != "$DEPLOY_DIR"/.ghcr-docker-config.* ||
    ! -d "$config_dir" || -L "$config_dir" ]]
  then
    echo "ERROR: refusing to remove an unsafe GHCR Docker config path" >&2
    return 1
  fi
  rm -rf -- "$config_dir"
  GHCR_DOCKER_CONFIG=""
  unset DOCKER_CONFIG
}

cleanup_ghcr_docker_config_on_exit() {
  local exit_code="${1:-1}"
  local cleanup_status=0

  trap - ERR EXIT HUP INT TERM
  cleanup_ghcr_docker_config || cleanup_status=$?
  if ((exit_code == 0 && cleanup_status != 0)); then
    exit_code=$cleanup_status
  fi
  exit "$exit_code"
}

initialize_ghcr_docker_config() {
  GHCR_DOCKER_CONFIG="$(mktemp -d "${DEPLOY_DIR}/.ghcr-docker-config.XXXXXX")"
  chmod 0700 "$GHCR_DOCKER_CONFIG"
  export DOCKER_CONFIG="$GHCR_DOCKER_CONFIG"
  trap 'cleanup_ghcr_docker_config_on_exit $?' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if ! printf '%s' "$GHCR_PULL_TOKEN" |
    docker login ghcr.io --username "$GHCR_PULL_USERNAME" --password-stdin >/dev/null
  then
    echo "ERROR: failed to authenticate GHCR image pulls" >&2
    return 1
  fi
  unset GHCR_PULL_TOKEN GHCR_PULL_USERNAME
}

stop_maintenance_window() {
  if [[ -z "$MAINTENANCE_MARKER_NAME" ]]; then
    return 0
  fi
  docker stop --time=1 "$MAINTENANCE_MARKER_NAME" >/dev/null 2>&1 || true
  docker rm -f "$MAINTENANCE_MARKER_NAME" >/dev/null 2>&1 || true
  MAINTENANCE_MARKER_NAME=""
}

start_maintenance_window() {
  local marker_image

  MAINTENANCE_MARKER_NAME="jobseek-maintenance-window-crawler-deploy-${JOBSEEK_DEPLOY_REVISION:0:12}"
  marker_image="$(docker inspect --format '{{.Image}}' "${COMPOSE_PROJECT_NAME}-redis-1" 2>/dev/null || true)"
  if [[ ! "$marker_image" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    docker pull "$REDIS_IMAGE" >/dev/null
    marker_image="$(docker image inspect --format '{{.Id}}' "$REDIS_IMAGE")"
  fi
  [[ "$marker_image" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: a local Redis image is required for the maintenance marker" >&2
    return 1
  }

  docker run --detach --rm \
    --name "$MAINTENANCE_MARKER_NAME" \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 16m \
    --cpus 0.05 \
    --pids-limit 16 \
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=maintenance-window \
    "$marker_image" \
    /bin/sh -c \
    "trap 'exit 0' TERM INT; sleep 28800" >/dev/null
}

verify_active_snapshot_file() {
  local snapshot="$1"
  local digest_file="$2"
  local label="$3"
  local expected actual

  [[ -f "$snapshot" && ! -L "$snapshot" ]] || {
    echo "ERROR: crawler-confirmed active ${label} snapshot is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$digest_file" && ! -L "$digest_file" ]] || {
    echo "ERROR: crawler-confirmed active ${label} digest is unavailable or unsafe" >&2
    return 1
  }
  expected="$(tr -d '[:space:]' <"$digest_file")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: crawler-confirmed active ${label} digest is invalid" >&2
    return 1
  }
  actual="$(sha256sum "$snapshot" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: crawler-confirmed active ${label} snapshot failed verification" >&2
    return 1
  }
}

verify_exact_csv_tree() {
  local data_dir="$1" files_manifest="$2"
  [[ -d "$data_dir" && ! -L "$data_dir" && \
    -f "$files_manifest" && ! -L "$files_manifest" ]] || {
    echo "ERROR: crawler active CSV snapshot is unavailable or unsafe" >&2
    return 1
  }
  python3 - "$data_dir" "$files_manifest" <<'PY'
import hashlib
import os
import pathlib
import re
import stat
import sys

root = pathlib.Path(sys.argv[1])
manifest = pathlib.Path(sys.argv[2])
rows = []
for directory, dirnames, filenames in os.walk(root, followlinks=False):
    directory_path = pathlib.Path(directory)
    for name in list(dirnames):
        path = directory_path / name
        if path.is_symlink():
            raise SystemExit(f"unsafe symlink in CSV snapshot: {path}")
    for name in filenames:
        path = directory_path / name
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise SystemExit(f"unsafe file in CSV snapshot: {path}")
        relative = path.relative_to(root).as_posix()
        if not re.fullmatch(r"[A-Za-z0-9._/-]+\.csv", relative):
            raise SystemExit(f"unexpected file in CSV snapshot: {relative}")
        rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
if not rows:
    raise SystemExit("CSV snapshot is empty")
actual = "".join(f"{digest}  {relative}\n" for relative, digest in sorted(rows))
if manifest.read_text(encoding="utf-8") != actual:
    raise SystemExit("CSV snapshot file manifest does not match exact tree")
PY
}

write_exact_csv_manifest() {
  local data_dir="$1" files_manifest="$2"
  python3 - "$data_dir" "$files_manifest" <<'PY'
import hashlib
import os
import pathlib
import re
import stat
import sys

root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
rows = []
for directory, dirnames, filenames in os.walk(root, followlinks=False):
    directory_path = pathlib.Path(directory)
    for name in list(dirnames):
        path = directory_path / name
        if path.is_symlink():
            raise SystemExit(f"unsafe symlink in CSV candidate: {path}")
    for name in filenames:
        path = directory_path / name
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise SystemExit(f"unsafe file in CSV candidate: {path}")
        relative = path.relative_to(root).as_posix()
        if not re.fullmatch(r"[A-Za-z0-9._/-]+\.csv", relative):
            raise SystemExit(f"unexpected file in CSV candidate: {relative}")
        rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
if not rows:
    raise SystemExit("CSV candidate is empty")
output.write_text(
    "".join(f"{digest}  {relative}\n" for relative, digest in sorted(rows)),
    encoding="utf-8",
)
PY
  chmod 0644 "$files_manifest"
}

read_exact_release_value() {
  local file="$1"
  local key="$2"
  local -a values=()

  [[ -f "$file" && ! -L "$file" ]] || return 1
  mapfile -t values < <(sed -n "s/^${key}=//p" "$file")
  (( ${#values[@]} == 1 )) || return 1
  printf '%s\n' "${values[0]}"
}

verify_runtime_contract_pair() {
  local environment_file="$1"
  local success_file="$2"
  local required="${3:-0}"
  local -a environment_values=() success_values=()

  mapfile -t environment_values < <(
    sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$environment_file"
  )
  mapfile -t success_values < <(
    sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$success_file"
  )
  if (( ${#environment_values[@]} == 0 && ${#success_values[@]} == 0 )); then
    if [[ "$required" == 0 ]]; then
      # Compatibility with pre-v3 release generations committed before issue #7996.
      return 0
    fi
    echo "ERROR: crawler runtime contract is required for v3 release evidence" >&2
    return 1
  fi
  (( ${#environment_values[@]} == 1 && ${#success_values[@]} == 1 )) || {
    echo "ERROR: crawler runtime contract is missing or duplicated" >&2
    return 1
  }
  [[ "${environment_values[0]}" =~ ^[0-9a-f]{64}$ &&
    "${environment_values[0]}" == "${success_values[0]}" ]] || {
    echo "ERROR: crawler runtime contract evidence disagrees" >&2
    return 1
  }
}

load_active_release() {
  local target

  [[ -d "$ACTIVE_RELEASE_ROOT" && ! -L "$ACTIVE_RELEASE_ROOT" ]] || {
    echo "ERROR: crawler active-release generation root is unavailable or unsafe" >&2
    return 1
  }
  [[ -L "$ACTIVE_RELEASE_POINTER" ]] || {
    echo "ERROR: crawler active-release pointer is unavailable or unsafe" >&2
    return 1
  }
  target="$(readlink "$ACTIVE_RELEASE_POINTER")"
  [[ "$target" == "$ACTIVE_RELEASE_ROOT/"* &&
    "${target#"$ACTIVE_RELEASE_ROOT/"}" =~ ^[a-zA-Z0-9._-]+$ ]] || {
    echo "ERROR: crawler active-release pointer escapes its generation root" >&2
    return 1
  }
  [[ -d "$target" && ! -L "$target" ]] || {
    echo "ERROR: crawler active-release generation is unavailable or unsafe" >&2
    return 1
  }

  ACTIVE_RELEASE_DIR="$target"
  ACTIVE_COMPOSE_SNAPSHOT="$target/docker-compose.yml"
  ACTIVE_COMPOSE_SNAPSHOT_SHA256="$target/docker-compose.sha256"
  ACTIVE_ENV_SNAPSHOT="$target/environment.env"
  ACTIVE_ENV_SNAPSHOT_SHA256="$target/environment.sha256"
  DEPLOY_SUCCESS_FILE="$target/success.env"
  ACTIVE_RELEASE_MANIFEST="$target/release.manifest"
  ACTIVE_RELEASE_FORMAT=""
  ACTIVE_IMAGE_OVERRIDE=""
  ACTIVE_DATA_SNAPSHOT=""
  ACTIVE_DATA_FILES_MANIFEST=""
}

verify_active_deploy_snapshot() {
  local format_version compose_digest env_digest success_digest actual_success_digest
  local bootstrap_legacy=""

  load_active_release || return 1
  verify_active_snapshot_file \
    "$ACTIVE_COMPOSE_SNAPSHOT" "$ACTIVE_COMPOSE_SNAPSHOT_SHA256" Compose || return 1
  verify_active_snapshot_file \
    "$ACTIVE_ENV_SNAPSHOT" "$ACTIVE_ENV_SNAPSHOT_SHA256" environment || return 1
  [[ "$(stat -c '%a' "$ACTIVE_ENV_SNAPSHOT")" == 600 ]] || {
    echo "ERROR: crawler-confirmed active environment snapshot permissions are unsafe" >&2
    return 1
  }
  [[ -f "$DEPLOY_SUCCESS_FILE" && ! -L "$DEPLOY_SUCCESS_FILE" ]] || {
    echo "ERROR: crawler-confirmed release marker is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$ACTIVE_RELEASE_MANIFEST" && ! -L "$ACTIVE_RELEASE_MANIFEST" ]] || {
    echo "ERROR: crawler active-release manifest is unavailable or unsafe" >&2
    return 1
  }
  format_version="$(
    read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" RELEASE_FORMAT_VERSION
  )" || return 1
  [[ "$format_version" == 1 || "$format_version" == 2 || "$format_version" == 3 ]] || {
    echo "ERROR: crawler active-release format is unsupported" >&2
    return 1
  }
  compose_digest="$(
    read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" COMPOSE_SHA256
  )" || return 1
  env_digest="$(
    read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" ENVIRONMENT_SHA256
  )" || return 1
  success_digest="$(
    read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" SUCCESS_SHA256
  )" || return 1
  [[ "$compose_digest" =~ ^[0-9a-f]{64}$ &&
    "$env_digest" =~ ^[0-9a-f]{64}$ &&
    "$success_digest" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: crawler active-release manifest contains an invalid digest" >&2
    return 1
  }
  [[ "$compose_digest" == "$(tr -d '[:space:]' <"$ACTIVE_COMPOSE_SNAPSHOT_SHA256")" &&
    "$env_digest" == "$(tr -d '[:space:]' <"$ACTIVE_ENV_SNAPSHOT_SHA256")" ]] || {
    echo "ERROR: crawler active-release manifest disagrees with its snapshots" >&2
    return 1
  }
  actual_success_digest="$(
    sha256sum "$DEPLOY_SUCCESS_FILE" | awk '{print $1}'
  )" || return 1
  [[ "$success_digest" == "$actual_success_digest" ]] || {
    echo "ERROR: crawler active-release marker failed verification" >&2
    return 1
  }

  ACTIVE_RELEASE_FORMAT="$format_version"
  if [[ "$format_version" == 2 ]]; then
    local image_override_digest actual_image_override_digest
    ACTIVE_IMAGE_OVERRIDE="$ACTIVE_RELEASE_DIR/rollback-images.override.yml"
    image_override_digest="$(
      read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" IMAGE_OVERRIDE_SHA256
    )" || return 1
    bootstrap_legacy="$(
      read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" BOOTSTRAP_LEGACY
    )" || return 1
    [[ "$image_override_digest" =~ ^[0-9a-f]{64}$ ]] || {
      echo "ERROR: crawler bootstrap image-override digest is invalid" >&2
      return 1
    }
    [[ "$bootstrap_legacy" == 0 || "$bootstrap_legacy" == 1 ]] || {
      echo "ERROR: crawler bootstrap generation kind is invalid" >&2
      return 1
    }
    [[ -f "$ACTIVE_IMAGE_OVERRIDE" && ! -L "$ACTIVE_IMAGE_OVERRIDE" ]] || {
      echo "ERROR: crawler bootstrap image override is unavailable or unsafe" >&2
      return 1
    }
    actual_image_override_digest="$(sha256sum "$ACTIVE_IMAGE_OVERRIDE" | awk '{print $1}')"
    [[ "$image_override_digest" == "$actual_image_override_digest" ]] || {
      echo "ERROR: crawler bootstrap image override failed verification" >&2
      return 1
    }
  fi

  if [[ "$format_version" == 3 ]]; then
    local data_files_digest data_contract data_revision
    ACTIVE_DATA_SNAPSHOT="$ACTIVE_RELEASE_DIR/data"
    ACTIVE_DATA_FILES_MANIFEST="$ACTIVE_RELEASE_DIR/data-files.sha256"
    data_files_digest="$(
      read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" DATA_FILES_SHA256
    )" || return 1
    data_contract="$(
      read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" DATA_CONTRACT_SHA256
    )" || return 1
    data_revision="$(
      read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" DATA_REVISION
    )" || return 1
    [[ "$data_files_digest" =~ ^[0-9a-f]{64}$ && \
      "$data_contract" =~ ^[0-9a-f]{64}$ && "$data_revision" =~ ^[0-9a-f]{40}$ ]] || {
      echo "ERROR: crawler active CSV identity is invalid" >&2
      return 1
    }
    [[ "$data_contract" == "$data_files_digest" ]] || {
      echo "ERROR: crawler active data contract does not match its exact tree" >&2
      return 1
    }
    [[ -f "$ACTIVE_DATA_FILES_MANIFEST" && ! -L "$ACTIVE_DATA_FILES_MANIFEST" && \
      "$data_files_digest" == "$(sha256sum "$ACTIVE_DATA_FILES_MANIFEST" | awk '{print $1}')" ]] || {
      echo "ERROR: crawler active CSV manifest failed verification" >&2
      return 1
    }
    verify_exact_csv_tree \
      "$ACTIVE_DATA_SNAPSHOT" "$ACTIVE_DATA_FILES_MANIFEST" || return 1
    local has_image_override
    local -a image_override_digests=()
    has_image_override="$(
      read_exact_release_value "$ACTIVE_RELEASE_MANIFEST" HAS_IMAGE_OVERRIDE
    )" || return 1
    mapfile -t image_override_digests < <(
      sed -n 's/^IMAGE_OVERRIDE_SHA256=//p' "$ACTIVE_RELEASE_MANIFEST"
    )
    [[ "$has_image_override" == 0 || "$has_image_override" == 1 ]] || return 1
    if [[ "$has_image_override" == 1 ]]; then
      local image_override_digest actual_image_override_digest
      ACTIVE_IMAGE_OVERRIDE="$ACTIVE_RELEASE_DIR/rollback-images.override.yml"
      (( ${#image_override_digests[@]} == 1 )) || return 1
      image_override_digest="${image_override_digests[0]}"
      [[ "$image_override_digest" =~ ^[0-9a-f]{64}$ && \
        -f "$ACTIVE_IMAGE_OVERRIDE" && ! -L "$ACTIVE_IMAGE_OVERRIDE" ]] || return 1
      actual_image_override_digest="$(sha256sum "$ACTIVE_IMAGE_OVERRIDE" | awk '{print $1}')"
      [[ "$image_override_digest" == "$actual_image_override_digest" ]] || return 1
    else
      (( ${#image_override_digests[@]} == 0 )) || return 1
      [[ ! -e "$ACTIVE_RELEASE_DIR/rollback-images.override.yml" && \
        ! -L "$ACTIVE_RELEASE_DIR/rollback-images.override.yml" ]] || {
        echo "ERROR: crawler active release contains unattested image-override residue" >&2
        return 1
      }
    fi
  fi

  local -a release_compose_args configured_images
  local configured_image configured_images_output
  release_compose_args=(-f "$ACTIVE_COMPOSE_SNAPSHOT")
  if [[ -n "$ACTIVE_IMAGE_OVERRIDE" ]]; then
    release_compose_args+=(-f "$ACTIVE_IMAGE_OVERRIDE")
  fi
  configured_images_output="$(
    env -i \
      "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}" \
      "HOME=${HOME:-$DEPLOY_DIR}" \
      "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME" \
      docker compose --env-file "$ACTIVE_ENV_SNAPSHOT" \
        "${release_compose_args[@]}" config --images
  )" || return 1
  [[ -n "$configured_images_output" ]] || {
    echo "ERROR: crawler active release declares no service images" >&2
    return 1
  }
  mapfile -t configured_images <<<"$configured_images_output"
  (( ${#configured_images[@]} > 0 )) || {
    echo "ERROR: crawler active release declares no service images" >&2
    return 1
  }
  for configured_image in "${configured_images[@]}"; do
    [[ "$configured_image" =~ @sha256:[0-9a-f]{64}$ ]] || {
      echo "ERROR: crawler active release contains a mutable service image" >&2
      return 1
    }
  done

  local -a identity_keys=(CRAWLER_IMAGE_TAG JOBSEEK_DEPLOY_REVISION)
  if [[ "$format_version" == 1 || "$format_version" == 3 ||
    ( "$format_version" == 2 && "$bootstrap_legacy" == 0 ) ]]
  then
    identity_keys=(
      CRAWLER_IMAGE_TAG CRAWLER_IMAGE_REF BROWSER_IMAGE_REF
      SHIM_IMAGE_REF JOBSEEK_DEPLOY_REVISION
    )
  fi
  local key env_value success_value
  for key in "${identity_keys[@]}"; do
    env_value="$(
      read_exact_release_value "$ACTIVE_ENV_SNAPSHOT" "$key"
    )" || return 1
    success_value="$(
      read_exact_release_value "$DEPLOY_SUCCESS_FILE" "$key"
    )" || return 1
    [[ -n "$env_value" && "$env_value" == "$success_value" ]] || {
      echo "ERROR: crawler active-release marker and environment disagree on ${key}" >&2
      return 1
    }
  done
  local runtime_contract_required=0
  if [[ "$format_version" == 3 ]]; then
    runtime_contract_required=1
  fi
  verify_runtime_contract_pair \
    "$ACTIVE_ENV_SNAPSHOT" "$DEPLOY_SUCCESS_FILE" "$runtime_contract_required" || return 1
  if [[ "$format_version" == 3 ]]; then
    python3 "$BRIDGE_VERIFIER" \
      --generation "$ACTIVE_RELEASE_DIR" --owner "$OWNER" >/dev/null || return 1
  fi
}

activate_release_generation() {
  local generation="$1"

  [[ -d "$ACTIVE_RELEASE_ROOT" && ! -L "$ACTIVE_RELEASE_ROOT" &&
    "$generation" == "$ACTIVE_RELEASE_ROOT/"* &&
    "${generation#"$ACTIVE_RELEASE_ROOT/"}" =~ ^[a-zA-Z0-9._-]+$ &&
    -d "$generation" && ! -L "$generation" ]] || return 1
  python3 - "$generation" "$ACTIVE_RELEASE_POINTER" "$DEPLOY_DIR" <<'PY'
import os
import secrets
import sys

generation, active, parent = sys.argv[1:]
temporary = f"{active}.{secrets.token_hex(16)}"
os.symlink(generation, temporary)
os.replace(temporary, active)
directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

publish_legacy_success_marker() {
  local temporary

  temporary="$(mktemp "${DEPLOY_DIR}/.crawler-deploy-success-link.XXXXXX")"
  rm -f "$temporary"
  ln "$DEPLOY_SUCCESS_FILE" "$temporary"
  mv "$temporary" "$LEGACY_DEPLOY_SUCCESS_FILE"
}

publish_active_deploy_release() {
  local success_file="$1"
  local data_dir="$2"
  local data_files_manifest="$3"
  local generation compose_digest env_digest success_digest data_files_digest

  [[ ! -e "$ACTIVE_RELEASE_ROOT" ||
    ( -d "$ACTIVE_RELEASE_ROOT" && ! -L "$ACTIVE_RELEASE_ROOT" ) ]] || {
    echo "ERROR: crawler active-release generation root is unsafe" >&2
    return 1
  }
  install -d -m 0700 "$ACTIVE_RELEASE_ROOT"
  generation="$(mktemp -d "${ACTIVE_RELEASE_ROOT}/release-${JOBSEEK_DEPLOY_REVISION}.XXXXXX")"
  install -m 0644 "$DEPLOY_DIR/docker-compose.yml" "$generation/docker-compose.yml"
  install -m 0600 "$ENV_FILE" "$generation/environment.env"
  install -m 0644 "$success_file" "$generation/success.env"
  install -d -m 0755 "$generation/data"
  cp -a "$data_dir/." "$generation/data/"
  install -m 0644 "$data_files_manifest" "$generation/data-files.sha256"
  verify_exact_csv_tree "$generation/data" "$generation/data-files.sha256"
  compose_digest="$(sha256sum "$generation/docker-compose.yml" | awk '{print $1}')"
  env_digest="$(sha256sum "$generation/environment.env" | awk '{print $1}')"
  success_digest="$(sha256sum "$generation/success.env" | awk '{print $1}')"
  data_files_digest="$(sha256sum "$generation/data-files.sha256" | awk '{print $1}')"
  [[ "$data_files_digest" == "$JOBSEEK_DATA_CONTRACT_SHA256" ]] || {
    echo "ERROR: crawler image data contract changed before release publication" >&2
    return 1
  }
  printf '%s\n' "$compose_digest" >"$generation/docker-compose.sha256"
  printf '%s\n' "$env_digest" >"$generation/environment.sha256"
  printf '%s\n' \
    'RELEASE_FORMAT_VERSION=3' \
    "COMPOSE_SHA256=$compose_digest" \
    "ENVIRONMENT_SHA256=$env_digest" \
    "SUCCESS_SHA256=$success_digest" \
    "DATA_FILES_SHA256=$data_files_digest" \
    "DATA_CONTRACT_SHA256=$data_files_digest" \
    "DATA_REVISION=$JOBSEEK_DEPLOY_REVISION" \
    'HAS_IMAGE_OVERRIDE=0' \
    >"$generation/release.manifest"
  chmod 0644 \
    "$generation/docker-compose.sha256" \
    "$generation/environment.sha256" \
    "$generation/release.manifest"

  python3 - "$generation" <<'PY'
import os
import sys

generation = sys.argv[1]
for directory, dirnames, filenames in os.walk(generation, topdown=False):
    for name in filenames:
        path = os.path.join(directory, name)
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
release_root = os.path.dirname(generation)
release_root_fd = os.open(release_root, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(release_root_fd)
finally:
    os.close(release_root_fd)
PY

  activate_release_generation "$generation"
  load_active_release
  verify_active_deploy_snapshot
}

prepare_forward_data_snapshot() {
  local staging_root container_id forward_contract status=0
  staging_root="$(mktemp -d "${DEPLOY_DIR}/.crawler-forward-data-${JOBSEEK_DEPLOY_REVISION}.XXXXXX")"
  FORWARD_DATA_STAGING_ROOT="$staging_root"
  FORWARD_DATA_SNAPSHOT="$staging_root/data"
  FORWARD_DATA_FILES_MANIFEST="$staging_root/data-files.sha256"
  install -d -m 0755 "$FORWARD_DATA_SNAPSHOT"
  container_id="$(docker create "$CRAWLER_IMAGE_REF")"
  if docker cp "$container_id:/app/data/." "$FORWARD_DATA_SNAPSHOT"; then
    :
  else
    status=$?
  fi
  docker rm "$container_id" >/dev/null || status=$?
  (( status == 0 )) || return "$status"
  find "$FORWARD_DATA_SNAPSHOT" -type f ! -name '*.csv' -delete
  find "$FORWARD_DATA_SNAPSHOT" -depth -type d -empty -delete
  write_exact_csv_manifest "$FORWARD_DATA_SNAPSHOT" "$FORWARD_DATA_FILES_MANIFEST"
  verify_exact_csv_tree "$FORWARD_DATA_SNAPSHOT" "$FORWARD_DATA_FILES_MANIFEST"
  forward_contract="$(sha256sum "$FORWARD_DATA_FILES_MANIFEST" | awk '{print $1}')"
  [[ "$forward_contract" == "$JOBSEEK_DATA_CONTRACT_SHA256" ]] || {
    echo "ERROR: crawler image CSV tree does not match the expected data contract" >&2
    return 1
  }
}

cleanup_forward_data_snapshot() {
  local staging_root
  [[ -n "$FORWARD_DATA_STAGING_ROOT" ]] || return 0
  staging_root="$FORWARD_DATA_STAGING_ROOT"
  [[ "$staging_root" == "$DEPLOY_DIR"/.crawler-forward-data-"$JOBSEEK_DEPLOY_REVISION".* && \
    -d "$staging_root" && ! -L "$staging_root" ]] || return 1
  rm -rf -- "$staging_root"
  FORWARD_DATA_STAGING_ROOT=""
  FORWARD_DATA_SNAPSHOT=""
  FORWARD_DATA_FILES_MANIFEST=""
}

snapshot_active_deploy_specs() {
  local snapshot_dir spec temporary

  for spec in "${DEPLOY_SPEC_FILES[@]}"; do
    [[ -f "$DEPLOY_DIR/$spec" ]] || {
      echo "ERROR: active deploy artifact is unavailable: ${spec}" >&2
      return 1
    }
  done

  snapshot_dir="$(mktemp -d "${DEPLOY_DIR}/.deploy-spec.snapshot.XXXXXX")"
  for spec in "${DEPLOY_SPEC_FILES[@]}"; do
    if ! install -D -p "$DEPLOY_DIR/$spec" "$snapshot_dir/$spec"; then
      rm -rf "$snapshot_dir"
      return 1
    fi
  done

  # The independently scheduled shim rollout can already have replaced the
  # live Compose file. Only the verified, crawler-confirmed snapshot is valid
  # rollback evidence; the first rollout must pre-seed it explicitly.
  if ! install -m 0644 "$ACTIVE_COMPOSE_SNAPSHOT" "$snapshot_dir/docker-compose.yml"; then
    rm -rf "$snapshot_dir"
    return 1
  fi

  temporary="$(mktemp "${DEPLOY_DIR}/.deploy-spec.rollback.XXXXXX.tar")"
  if ! tar -C "$snapshot_dir" -cpf "$temporary" "${DEPLOY_SPEC_FILES[@]}"; then
    rm -rf "$snapshot_dir"
    rm -f "$temporary"
    return 1
  fi
  rm -rf "$snapshot_dir"
  chmod 600 "$temporary"
  mv "$temporary" "$ROLLBACK_SPEC_ARCHIVE"
}

activate_staged_deploy_specs() {
  local mode spec

  for spec in "${DEPLOY_SPEC_FILES[@]}"; do
    case "$spec" in
      deploy.sh | deploy_helpers.sh | scripts/*.py) mode=0755 ;;
      *) mode=0644 ;;
    esac
    install -D -m "$mode" "$INCOMING_DIR/$spec" "$DEPLOY_DIR/$spec"
  done
  install -m 0644 "$ROLLBACK_POOL_OVERRIDE_SOURCE" "$ROLLBACK_POOL_OVERRIDE"
}

restore_previous_deploy_specs() {
  [[ -f "$ROLLBACK_SPEC_ARCHIVE" && ! -L "$ROLLBACK_SPEC_ARCHIVE" ]] || {
    echo "ERROR: rollback deployment archive is unavailable or unsafe" >&2
    return 1
  }
  tar -C "$DEPLOY_DIR" -xpf "$ROLLBACK_SPEC_ARCHIVE"
}

reconciliation_wrapper_is_compatible() {
  local actual_wrapper_sha256

  [[ -x /usr/local/sbin/jobseek-reconciliation-state ]] || return 1
  [[ -r /usr/local/sbin/jobseek-crawler-reconciliation ]] || return 1
  actual_wrapper_sha256="$({
    sha256sum /usr/local/sbin/jobseek-crawler-reconciliation
  } | awk '{print $1}')"
  [[ "$actual_wrapper_sha256" == "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256" ]] || return 1
  /usr/local/sbin/jobseek-reconciliation-state check \
    --expected-wrapper-sha256 "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256" \
    >/dev/null 2>&1 || return 1
  systemctl is-enabled --quiet jobseek-crawler-reconciliation.timer || return 1
  systemctl is-active --quiet jobseek-crawler-reconciliation.timer || return 1
}

ensure_reconciliation_wrapper_compatible() {
  local deadline=$((SECONDS + ${RECONCILIATION_COMPAT_WAIT_SECONDS:-1200}))

  while (( SECONDS < deadline )); do
    if reconciliation_wrapper_is_compatible; then
      echo "Installed reconciliation wrapper matches the required contract" >&2
      return 0
    fi
    echo "Waiting for the compatible reconciliation host wrapper" >&2
    sleep 10
  done

  echo "ERROR: compatible reconciliation host wrapper was not installed before timeout" >&2
  return 1
}

configure_rollback_compose_contract() {
  local compose_file_count compose_file_value compose_files expected

  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
    echo "ERROR: restored rollback environment is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$DEPLOY_DIR/docker-compose.yml" && ! -L "$DEPLOY_DIR/docker-compose.yml" ]] || {
    echo "ERROR: restored rollback Compose file is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$ROLLBACK_POOL_OVERRIDE" && ! -L "$ROLLBACK_POOL_OVERRIDE" ]] || {
    echo "ERROR: rollback pool-budget override is unavailable or unsafe" >&2
    return 1
  }
  if [[ -n "${ROLLBACK_ACTIVE_IMAGE_OVERRIDE:-}" &&
    ( ! -f "$ROLLBACK_ACTIVE_IMAGE_OVERRIDE" || -L "$ROLLBACK_ACTIVE_IMAGE_OVERRIDE" ) ]]
  then
    echo "ERROR: rollback image-identity override is unavailable or unsafe" >&2
    return 1
  fi

  compose_files="$DEPLOY_DIR/docker-compose.yml"
  if [[ -n "${ROLLBACK_ACTIVE_IMAGE_OVERRIDE:-}" ]]; then
    compose_files+="${compose_files:+:}$ROLLBACK_ACTIVE_IMAGE_OVERRIDE"
  fi
  compose_files+="${compose_files:+:}$ROLLBACK_POOL_OVERRIDE"
  expected="COMPOSE_FILE=$compose_files"
  compose_file_count="$(grep -Ec '^COMPOSE_FILE=' "$ENV_FILE" || true)"
  compose_file_value="$(grep -E '^COMPOSE_FILE=' "$ENV_FILE" || true)"
  if ((compose_file_count == 0)); then
    printf '\n%s\n' "$expected" >>"$ENV_FILE" || return 1
  elif ((compose_file_count != 1)) || [[ "$compose_file_value" != "$expected" ]]; then
    echo "ERROR: restored rollback environment has an unexpected Compose contract" >&2
    return 1
  fi
  chmod 600 "$ENV_FILE"
}

rollback_compose() {
  local -a compose_args
  local -a clean_environment=(
    "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}"
    "HOME=${HOME:-$DEPLOY_DIR}"
    "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
  )

  # Compose gives process variables precedence over the restored env file.
  # Run rollback in a clean environment so the failed release tag and every
  # other current SSH input cannot override the previous deployment contract.
  compose_args=(-f "$DEPLOY_DIR/docker-compose.yml")
  if [[ -n "${ROLLBACK_ACTIVE_IMAGE_OVERRIDE:-}" ]]; then
    compose_args+=(-f "$ROLLBACK_ACTIVE_IMAGE_OVERRIDE")
  fi
  compose_args+=(-f "$ROLLBACK_POOL_OVERRIDE")
  if [[ -n "${DOCKER_CONFIG:-}" ]]; then
    clean_environment+=("DOCKER_CONFIG=$DOCKER_CONFIG")
  fi
  if [[ -n "${ROLLBACK_SYNC_WEB_DATABASE_URL:-}" ]]; then
    clean_environment+=("WEB_DATABASE_URL=$ROLLBACK_SYNC_WEB_DATABASE_URL")
  fi
  env -i "${clean_environment[@]}" \
    docker compose \
    --env-file "$ENV_FILE" \
    "${compose_args[@]}" \
    "$@"
}

rollback_sync_previous_config() {
  local crawler_ref restored_web_database_url status
  local -a data_args=()

  crawler_ref="$(read_exact_release_value "$ENV_FILE" CRAWLER_IMAGE_REF)" || {
    echo "ERROR: restored crawler image identity is unavailable for config rollback" >&2
    return 1
  }
  [[ "$crawler_ref" =~ ^ghcr\.io/${OWNER}/jobseek-crawler@sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: restored crawler image identity is invalid for config rollback" >&2
    return 1
  }
  restored_web_database_url="$(
    read_exact_release_value "$ENV_FILE" WEB_DATABASE_URL
  )" || {
    echo "ERROR: restored web database credential is unavailable for config rollback" >&2
    return 1
  }
  [[ -n "$restored_web_database_url" ]] || {
    echo "ERROR: restored web database credential is empty for config rollback" >&2
    return 1
  }

  ROLLBACK_SYNC_WEB_DATABASE_URL="$restored_web_database_url"
  if [[ "${ACTIVE_RELEASE_FORMAT:-}" == 3 ]]; then
    verify_exact_csv_tree "${ACTIVE_DATA_SNAPSHOT:-}" "${ACTIVE_DATA_FILES_MANIFEST:-}"
    data_args=(-v "${ACTIVE_DATA_SNAPSHOT}:/app/data:ro")
  else
    echo "ERROR: exact previous CSV rollback evidence is unavailable" >&2
    return 1
  fi
  if rollback_compose run --rm --no-deps \
    "${data_args[@]}" \
    -e WEB_DATABASE_URL \
    -e CRAWLER_DB_ROLE=rollback-sync \
    -e CRAWLER_DB_POOL_MIN=0 \
    -e CRAWLER_DB_POOL_MAX=4 \
    worker-1 \
    uv run --no-sync crawler sync
  then
    status=0
  else
    status=$?
  fi
  ROLLBACK_SYNC_WEB_DATABASE_URL=""
  if (( status != 0 )); then
    echo "ERROR: previous crawler configuration could not be restored" >&2
    return "$status"
  fi
}

rollback_compose_service_ready() {
  local service="$1"
  local container_id state health

  container_id="$(rollback_compose ps -q "$service" 2>/dev/null)" || return 1
  [[ -n "$container_id" && "$(wc -w <<<"$container_id")" -eq 1 ]] || return 1
  state="$(docker inspect -f '{{.State.Status}}' "$container_id" 2>/dev/null)" || return 1
  [[ "$state" == "running" ]] || return 1
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null)" || return 1
  [[ "$health" == "none" || "$health" == "healthy" ]] || return 1
  if [[ "$service" == "alloy" ]]; then
    curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:12346/-/ready >/dev/null
  fi
}

wait_for_rollback_core_services() {
  local services=(redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy)
  local deadline=$((SECONDS + ${ROLLBACK_HEALTH_TIMEOUT_SECONDS:-180}))
  local service
  local missing=()

  while ((SECONDS < deadline)); do
    missing=()
    for service in "${services[@]}"; do
      if ! rollback_compose_service_ready "$service"; then
        missing+=("$service")
      fi
    done
    if ((${#missing[@]} == 0)); then
      return 0
    fi
    echo "Waiting for rollback services to become ready: ${missing[*]}" >&2
    sleep 5
  done

  echo "ERROR: rollback services did not become ready: ${missing[*]}" >&2
  rollback_compose ps >&2 || true
  return 1
}

rollback_deploy() {
  local exit_code="${1:-1}"
  local command_status=0
  local rollback_status=0
  local quiesce_complete=0
  local release_restore_complete=0
  local env_restore_complete=0
  local spec_restore_complete=0
  local bounded_contract_persisted=0
  local config_restore_complete=0
  local rollback_stack_started=0

  trap - ERR EXIT HUP INT TERM
  if declare -F cleanup_ghcr_docker_config_on_exit >/dev/null; then
    trap 'cleanup_ghcr_docker_config_on_exit $?' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
  fi
  if (( ! ROLLBACK_ARMED || ROLLBACK_RUNNING )); then
    exit "$exit_code"
  fi
  ROLLBACK_RUNNING=1
  set +e
  echo "Deploy failed — restoring crawler containers on previous image" >&2

  cd "$DEPLOY_DIR"
  command_status=$?
  if ((command_status != 0)); then
    rollback_status=$command_status
  else
    # Stop every local-PostgreSQL crawler owner before restoring the archived
    # spec. Starting old and new generations together would violate the same
    # budget that this rollback is responsible for preserving.
    docker compose \
      --env-file "$ENV_FILE" \
      -f "$DEPLOY_DIR/docker-compose.yml" \
      stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain
    command_status=$?
    if ((command_status == 0)); then
      quiesce_complete=1
    fi
  fi
  if ((command_status != 0 && rollback_status == 0)); then
    rollback_status=$command_status
  fi

  if [[ -n "$ROLLBACK_ACTIVE_RELEASE_TARGET" ]]; then
    activate_release_generation "$ROLLBACK_ACTIVE_RELEASE_TARGET"
    command_status=$?
    if ((command_status == 0)); then
      verify_active_deploy_snapshot
      command_status=$?
    fi
  else
    echo "ERROR: crawler rollback release generation is unavailable" >&2
    command_status=1
  fi
  if ((command_status == 0)); then
    release_restore_complete=1
  elif ((rollback_status == 0)); then
    rollback_status=$command_status
  fi

  if ((ENV_FILE_WAS_PRESENT)); then
    if [[ -f "$ROLLBACK_ENV_FILE" && ! -L "$ROLLBACK_ENV_FILE" ]]; then
      mv "$ROLLBACK_ENV_FILE" "$ENV_FILE"
      command_status=$?
      if ((command_status == 0)); then
        chmod 600 "$ENV_FILE"
        command_status=$?
      fi
    else
      echo "ERROR: rollback environment snapshot is unavailable or unsafe" >&2
      command_status=1
    fi
  else
    rm -f "$ENV_FILE"
    command_status=$?
  fi
  if ((command_status == 0)); then
    env_restore_complete=1
  fi
  if ((command_status != 0 && rollback_status == 0)); then
    rollback_status=$command_status
  fi

  restore_previous_deploy_specs
  command_status=$?
  if ((command_status == 0)); then
    spec_restore_complete=1
  fi
  if ((command_status != 0 && rollback_status == 0)); then
    rollback_status=$command_status
  fi

  # Persist the bounded old-stack contract whenever both authoritative
  # rollback inputs were restored, even if quiescing returned nonzero. A
  # partial stop must prevent restart, but it must not leave later operator
  # recovery pointed at the unbounded pre-budget Compose contract.
  if ((env_restore_complete && spec_restore_complete)); then
    configure_rollback_compose_contract
    command_status=$?
    if ((command_status == 0)); then
      bounded_contract_persisted=1
    elif ((rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi
  if (( ! quiesce_complete && bounded_contract_persisted )); then
    echo "ERROR: rollback quiesce was incomplete; bounded old-stack contract persisted but restart skipped" >&2
  fi
  if ((quiesce_complete && release_restore_complete && env_restore_complete && spec_restore_complete && bounded_contract_persisted)); then
    if (( ${FORWARD_SYNC_STARTED:-0} )); then
      rollback_sync_previous_config
      command_status=$?
      if ((command_status != 0 && rollback_status == 0)); then
        rollback_status=$command_status
      elif ((command_status == 0)); then
        config_restore_complete=1
      fi
    else
      config_restore_complete=1
    fi
  fi
  if (( ! config_restore_complete && ${FORWARD_SYNC_STARTED:-0} )); then
    echo "ERROR: rollback config restore was incomplete; old stack restart skipped" >&2
  fi
  if ((quiesce_complete && release_restore_complete && env_restore_complete && spec_restore_complete && bounded_contract_persisted && config_restore_complete)); then
    rollback_compose up -d --remove-orphans
    command_status=$?
    if ((command_status == 0)); then
      rollback_stack_started=1
    elif ((rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi
  if ((rollback_stack_started)); then
    wait_for_rollback_core_services
    command_status=$?
    if ((command_status != 0 && rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi

  if ((rollback_status == 0)); then
    verify_active_deploy_snapshot
    command_status=$?
    if ((command_status != 0)); then
      echo "ERROR: crawler rollback release generation failed final verification" >&2
      rollback_status=$command_status
    fi
  fi
  if declare -F cleanup_forward_data_snapshot >/dev/null; then
    cleanup_forward_data_snapshot
    command_status=$?
    if ((command_status != 0 && rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi
  if ((rollback_status == 0)); then
    publish_legacy_success_marker || {
      echo "WARNING: crawler rollback could not refresh its legacy release marker" >&2
    }
  fi
  if ((rollback_status != 0)); then
    echo "ERROR: retaining the last verified crawler snapshot after rollback failure" >&2
  fi

  stop_maintenance_window
  ROLLBACK_ARMED=0
  if ((rollback_status == 0)); then
    rm -f "$ROLLBACK_ENV_FILE" "$ROLLBACK_SPEC_ARCHIVE"
    command_status=$?
    if ((command_status != 0)); then
      echo "WARNING: crawler rollback could not remove temporary snapshots" >&2
    fi
  fi
  if ((rollback_status != 0)); then
    echo "ERROR: crawler rollback failed with status ${rollback_status}" >&2
    exit "$rollback_status"
  fi
  echo "Crawler rollback restored the previous image with bounded PostgreSQL pools" >&2
  exit "$exit_code"
}

arm_deploy_rollback() {
  ROLLBACK_ARMED=1
  trap 'rollback_deploy $?' ERR
  trap 'rollback_deploy $?' EXIT
  trap 'rollback_deploy 129' HUP
  trap 'rollback_deploy 130' INT
  trap 'rollback_deploy 143' TERM
}

disarm_deploy_rollback() {
  ROLLBACK_ARMED=0
  cleanup_ghcr_docker_config
  trap - ERR EXIT HUP INT TERM
}

compose_service_ready() {
  local service="$1"
  local container_id state health

  container_id="$(docker compose ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    return 1
  fi

  state="$(docker inspect -f '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
  if [[ "$state" != "running" ]]; then
    return 1
  fi

  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
  [[ "$health" == "none" || "$health" == "healthy" ]] || return 1
  if [[ "$service" == "alloy" ]]; then
    curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:12346/-/ready >/dev/null
  fi
}

wait_for_core_services() {
  local services=(redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy)
  local deadline=$((SECONDS + 180))
  local missing=()

  while (( SECONDS < deadline )); do
    missing=()
    for service in "${services[@]}"; do
      if ! compose_service_ready "$service"; then
        missing+=("$service")
      fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
      return 0
    fi

    echo "Waiting for services to become ready: ${missing[*]}" >&2
    sleep 5
  done

  echo "ERROR: services did not become ready: ${missing[*]}" >&2
  docker compose ps >&2 || true
  return 1
}

normalize_alloy_state_volume() {
  local volume_name="$1"

  # The long-running collector is explicit root with every capability dropped.
  # Make it the volume owner so it can write its WAL/cursors without relying on
  # CAP_DAC_OVERRIDE. The helper is pinned, networkless, and exits immediately.
  docker run --rm --network none --user 0:0 \
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=deploy-alloy-state \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c 'chown -R 0:0 /data-alloy && chmod 0700 /data-alloy'
}

prepare_alloy_state_volume() {
  local volume_name="${COMPOSE_PROJECT_NAME}_alloy-data"
  local marker="/data-alloy/.jobseek-persistent-state"
  local alloy_container marker_status state state_volume staging

  docker volume create "$volume_name" >/dev/null
  alloy_container="$(docker compose ps -aq alloy 2>/dev/null || true)"
  state=""
  state_volume=""
  if [[ -n "$alloy_container" ]]; then
    state="$(docker inspect -f '{{.State.Status}}' "$alloy_container" 2>/dev/null || true)"
    state_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data-alloy"}}{{.Name}}{{end}}{{end}}' "$alloy_container" 2>/dev/null || true)"
  fi

  # Fast path for every deploy after the migration. The marker lives in the
  # named volume, so force-recreating Alloy below cannot erase it or the
  # Docker-source positions stored beside it.
  marker_status="$(docker run --rm --network none --user 0:0 \
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=deploy-alloy-state \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c "if test -f '${marker}'; then echo present; else echo missing; fi")"
  if [[ "$marker_status" == "present" ]]; then
    echo "Alloy state volume already initialized: ${volume_name}" >&2
    if [[ "$state" != "running" || "$state_volume" != "$volume_name" ]]; then
      # A prior deploy may have prepared the volume and failed before the
      # changed service spec became active. Recreate immediately on retry.
      ALLOY_STATE_ACTIVATION_REQUIRED=1
    fi
    normalize_alloy_state_volume "$volume_name"
    return 0
  fi
  if [[ "$marker_status" != "missing" ]]; then
    echo "ERROR: unexpected Alloy state marker probe result" >&2
    return 1
  fi

  if [[ -n "$alloy_container" ]]; then
    # Stop cleanly so positions.yml and the remote-write WAL are consistent,
    # then stage the disposable container-layer state on the host before
    # writing anything into the new named volume. This ordering prevents the
    # first persistent-state rollout from causing one final historical replay.
    if [[ "$state" == "running" ]]; then
      docker stop --time=30 "$alloy_container" >/dev/null
    fi

    staging="$(mktemp -d "${DEPLOY_DIR}/.alloy-state.XXXXXX")"
    if ! docker cp "${alloy_container}:/data-alloy/." "$staging/"; then
      rm -rf "$staging"
      echo "ERROR: failed to stage current Alloy state" >&2
      return 1
    fi
    if ! docker run --rm --network none --user 0:0 \
      "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
      --label com.docker.compose.service=deploy-alloy-state \
      -v "${staging}:/source:ro" \
      -v "${volume_name}:/data-alloy" \
      --entrypoint sh "$ALLOY_IMAGE" \
      -c 'tar -C /source -cf - . | tar -C /data-alloy -xpf -'; then
      rm -rf "$staging"
      echo "ERROR: failed to seed persistent Alloy state" >&2
      return 1
    fi
    rm -rf "$staging"
    echo "Migrated Alloy state from ${alloy_container} into ${volume_name}" >&2
    ALLOY_STATE_ACTIVATION_REQUIRED=1
  else
    echo "No existing Alloy container; initializing an empty state volume" >&2
  fi

  docker run --rm --network none --user 0:0 \
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=deploy-alloy-state \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c "touch '${marker}'"
  normalize_alloy_state_volume "$volume_name"
}

deploy_disk_free_kb() {
  df -Pk "$DEPLOY_DIR" | awk 'NR == 2 {print $4}'
}

ensure_deploy_disk_headroom() {
  local free_kb

  free_kb="$(deploy_disk_free_kb)"
  if (( free_kb < DEPLOY_PRUNE_FREE_KB )); then
    echo "Low deploy disk headroom (${free_kb} KiB available); pruning Docker builder cache" >&2
    # The crawler host should run pulled images, not depend on local build
    # cache.
    docker builder prune -af >/dev/null || true
    free_kb="$(deploy_disk_free_kb)"
  fi

  if (( free_kb < DEPLOY_MIN_FREE_KB )); then
    echo "Deploy disk still low (${free_kb} KiB available); pruning unused Docker images" >&2
    # This preflight runs before pull/quiesce, so the active crawler and
    # browser images are still protected by running containers. Remove every
    # other image; rollback can pull its immutable digest with the fresh GHCR
    # credentials if it is ever needed after release mutation begins.
    docker image prune -af >/dev/null || true
    free_kb="$(deploy_disk_free_kb)"
  fi

  if (( free_kb < DEPLOY_MIN_FREE_KB )); then
    echo "ERROR: insufficient deploy disk headroom (${free_kb} KiB available; need ${DEPLOY_MIN_FREE_KB} KiB)" >&2
    df -h "$DEPLOY_DIR" >&2 || true
    docker system df >&2 || true
    return 1
  fi

  echo "Deploy disk headroom OK: ${free_kb} KiB available" >&2
}

resolve_shim_image_ref() {
  local candidate="$SHIM_IMAGE_REF"
  local existing_container configured_ref persisted_ref
  local -a persisted_refs=()

  # A coupled Murmur/crawler rollout passes the attested same-revision digest
  # explicitly. Never replace it with live state: a prior crawler attempt may
  # have failed and rolled the host back to the previous shim release.
  if [[ -n "$candidate" ]]; then
    if [[ ! "$candidate" =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]]; then
      echo "ERROR: SHIM_IMAGE_REF must be an immutable jobseek-murmur-shim digest" >&2
      return 1
    fi
    export SHIM_IMAGE_REF
    return 0
  fi

  # Crawler-only revisions do not build a same-head shim. Resolve their shim
  # from the live environment only when the running container agrees exactly;
  # accepting either source alone would allow drift to become the next release.
  if [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
    mapfile -t persisted_refs < <(sed -n 's/^SHIM_IMAGE_REF=//p' "$ENV_FILE")
    if (( ${#persisted_refs[@]} != 1 )); then
      echo "ERROR: active deploy environment must contain exactly one SHIM_IMAGE_REF value" >&2
      return 1
    fi
    persisted_ref="${persisted_refs[0]}"
  else
    echo "ERROR: active deploy environment is unavailable for SHIM_IMAGE_REF resolution" >&2
    return 1
  fi

  existing_container="${COMPOSE_PROJECT_NAME}-murmur-shim-1"
  configured_ref="$(
    docker inspect "$existing_container" --format '{{.Config.Image}}' 2>/dev/null || true
  )"
  if [[ ! "$persisted_ref" =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]] ||
    [[ "$configured_ref" != "$persisted_ref" ]]
  then
    echo "ERROR: live environment and Murmur container do not attest one immutable image" >&2
    return 1
  fi
  SHIM_IMAGE_REF="$persisted_ref"
  export SHIM_IMAGE_REF
}

read_exact_shim_ref() {
  local file="$1"
  local label="$2"
  local -a refs=()

  if [[ ! -f "$file" || -L "$file" ]]; then
    echo "ERROR: ${label} is not a regular non-symlink file" >&2
    return 1
  fi
  mapfile -t refs < <(sed -n 's/^SHIM_IMAGE_REF=//p' "$file")
  if (( ${#refs[@]} != 1 )); then
    echo "ERROR: ${label} must contain exactly one SHIM_IMAGE_REF value" >&2
    return 1
  fi
  if [[ ! "${refs[0]}" =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: ${label} contains a malformed SHIM_IMAGE_REF value" >&2
    return 1
  fi
  printf '%s\n' "${refs[0]}"
}

verify_shim_deploy_contract() {
  local success_file="$1"
  local live_ref success_ref container_id container_ref

  live_ref="$(read_exact_shim_ref "$ENV_FILE" "active deploy environment")"
  success_ref="$(read_exact_shim_ref "$success_file" "crawler success marker")"
  container_id="$(docker compose ps -aq murmur-shim 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    echo "ERROR: Murmur deploy contract found no live container" >&2
    return 1
  fi
  container_ref="$(docker inspect "$container_id" --format '{{.Config.Image}}')"
  if [[ "$live_ref" != "$SHIM_IMAGE_REF" ||
    "$container_ref" != "$SHIM_IMAGE_REF" ||
    "$success_ref" != "$SHIM_IMAGE_REF" ]]
  then
    echo "ERROR: Murmur live environment, container, and success marker disagree" >&2
    return 1
  fi
}

verify_compose_service_image() {
  local service="$1"
  local expected_ref="$2"
  local container_id actual_ref

  container_id="$(docker compose ps -aq "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    echo "ERROR: image identity check found no container for ${service}" >&2
    return 1
  fi
  actual_ref="$(docker inspect "$container_id" --format '{{.Config.Image}}')"
  if [[ "$actual_ref" != "$expected_ref" ]]; then
    echo "ERROR: ${service} image identity does not match the deployment manifest" >&2
    return 1
  fi
}

verify_deployed_image_identity() {
  local service

  verify_compose_service_image redis "$REDIS_IMAGE"
  for service in worker-1 worker-2 worker-3 exporter drain murmur-shim-runtime-init; do
    verify_compose_service_image "$service" "$CRAWLER_IMAGE_REF"
  done
  verify_compose_service_image browser-1 "$BROWSER_IMAGE_REF"
  verify_compose_service_image murmur-shim "$SHIM_IMAGE_REF"
  verify_compose_service_image alloy "$ALLOY_IMAGE"
}

running_compose_oneoff_containers() {
  docker ps \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
    --filter "label=com.docker.compose.oneoff=True" \
    --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Label "com.docker.compose.service"}}\t{{.Command}}'
}

ensure_no_running_compose_oneoffs() {
  local rows

  rows="$(running_compose_oneoff_containers)"
  if [[ -z "$rows" ]]; then
    echo "No running Docker Compose one-off containers detected for project ${COMPOSE_PROJECT_NAME}" >&2
    return 0
  fi

  cat >&2 <<EOF
ERROR: running Docker Compose one-off containers detected for project ${COMPOSE_PROJECT_NAME}.
Deploy is refusing to overlap a production one-off job because it may keep
running older crawler code while this deploy restarts services and reseeds
Redis-backed schedules.

Container ID\tName\tImage\tStatus\tCompose service\tCommand
${rows}

Wait for the one-off job to finish, or intentionally stop it after confirming
with the operator who started it, then rerun the deploy.
EOF
  return 1
}

running_typesense_maintenance_containers() {
  docker ps \
    --filter 'name=^/crawler-(backfill|refresh)-typesense-' \
    --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Command}}'
}

ensure_no_running_typesense_maintenance() {
  local rows

  rows="$(running_typesense_maintenance_containers)"
  if [[ -z "$rows" ]]; then
    echo "No running Typesense maintenance containers detected" >&2
    return 0
  fi

  cat >&2 <<EOF
ERROR: running Typesense maintenance containers detected.
Deploy is refusing to overlap a full backfill or count refresh because its
inline crawler sync also refreshes Typesense and could publish partial counts.

Container ID\tName\tImage\tStatus\tCommand
${rows}

Wait for the maintenance job to finish, then rerun the deploy.
EOF
  return 1
}

# Fail before touching services if an operator one-off is still running.
# Example: `docker compose run --rm worker-1 uv run --no-sync crawler ...`
# receives the Compose label `com.docker.compose.oneoff=True` and otherwise
# survives the named-service stop/recreate sequence below.
ensure_no_running_compose_oneoffs
ensure_no_running_typesense_maintenance
ensure_reconciliation_wrapper_compatible
python3 "$INCOMING_DIR/scripts/postgresql-operational-preflight.py"
initialize_ghcr_docker_config

# Resolve any interrupted data-only transaction before inspecting or mutating
# the active generation. On the first v3 rollout, reapply the exact pre-push
# main CSV snapshot with the committed old runtime and atomically bind it as
# rollback evidence. Never fall back to the older image's embedded CSVs.
bash "$INCOMING_DIR/scripts/crawler-csv-sync-host.sh" --recover-only
bash "$INCOMING_DIR/scripts/crawler-csv-sync-host.sh" \
  --bootstrap-current \
  "$JOBSEEK_PREVIOUS_DATA_REVISION" \
  "$JOBSEEK_PREVIOUS_RUNTIME_CONTRACT_SHA256" \
  "$JOBSEEK_PREVIOUS_DATA_CONTRACT_SHA256" \
  "$JOBSEEK_PREVIOUS_DATA_CANDIDATE_ID" \
  "$JOBSEEK_PREVIOUS_DATA_ARCHIVE_SHA256"

verify_active_deploy_snapshot
ROLLBACK_ACTIVE_RELEASE_TARGET="$ACTIVE_RELEASE_DIR"
ROLLBACK_ACTIVE_IMAGE_OVERRIDE="$ACTIVE_IMAGE_OVERRIDE"

# Snapshot the complete active deployment contract before replacing any file
# or credential. Rollback restores the old Compose spec and old env together,
# so an old image never starts with the new credential semantics.
rm -f "$ROLLBACK_ENV_FILE" "$ROLLBACK_SPEC_ARCHIVE"
snapshot_active_deploy_specs
ENV_FILE_WAS_PRESENT=1
install -m 0600 "$ACTIVE_ENV_SNAPSHOT" "$ROLLBACK_ENV_FILE"
arm_deploy_rollback
activate_staged_deploy_specs
resolve_shim_image_ref

# ── Stop any manually-started containers that conflict with compose ──
# `indexnow` was retired in #2821 (companies left the index); the rm is
# kept here to clean up boxes that still have a manually-started one.
legacy_containers=(redis worker-1 worker-2 worker-3 browser-1 exporter drain indexnow alloy)
docker stop --time=60 "${legacy_containers[@]}" 2>/dev/null || true
docker rm "${legacy_containers[@]}" 2>/dev/null || true

# ── Write env file ──────────────────────────────────────────────────
# Proxy vars are expanded with ``:-`` defaults so missing provider
# secrets don't break the deploy — PROXY_PROVIDER=none disables the
# proxy layer even when the URL envs are empty.

cat > "$ENV_FILE" <<EOF
OWNER=${OWNER}
CRAWLER_IMAGE_TAG=${IMAGE_TAG}
CRAWLER_IMAGE_REF=${CRAWLER_IMAGE_REF}
BROWSER_IMAGE_REF=${BROWSER_IMAGE_REF}
SHIM_IMAGE_REF=${SHIM_IMAGE_REF}
JOBSEEK_DEPLOY_REVISION=${JOBSEEK_DEPLOY_REVISION}
JOBSEEK_RUNTIME_CONTRACT_SHA256=${JOBSEEK_RUNTIME_CONTRACT_SHA256}
WEB_DATABASE_URL=${WEB_DATABASE_URL}
LOCAL_DATABASE_URL=${LOCAL_DATABASE_URL}
R2_ACCESS_KEY_ID=${R2_ACCESS_KEY_ID}
R2_SECRET_ACCESS_KEY=${R2_SECRET_ACCESS_KEY}
R2_ENDPOINT_URL=${R2_ENDPOINT_URL}
R2_DOMAIN_URL=${R2_DOMAIN_URL}
R2_BUCKET=${R2_BUCKET}
GRAFANA_PROM_URL=${GRAFANA_PROM_URL}
GRAFANA_PROM_USERNAME=${GRAFANA_PROM_USERNAME}
GRAFANA_PROM_PASSWORD=${GRAFANA_PROM_PASSWORD}
GRAFANA_LOKI_URL=${GRAFANA_LOKI_URL}
GRAFANA_LOKI_USERNAME=${GRAFANA_LOKI_USERNAME}
GRAFANA_LOKI_PASSWORD=${GRAFANA_LOKI_PASSWORD}
TYPESENSE_HOST=${TYPESENSE_HOST}
TYPESENSE_PORT=${TYPESENSE_PORT}
TYPESENSE_PROTOCOL=${TYPESENSE_PROTOCOL}
TYPESENSE_OPERATIONS_KEY=${TYPESENSE_OPERATIONS_KEY}
PROXY_PROVIDER=${PROXY_PROVIDER:-none}
WEBSHARE_PROXY_URL=${WEBSHARE_PROXY_URL:-}
DECODO_PROXY_URL=${DECODO_PROXY_URL:-}
MURMUR_TOKEN=${MURMUR_TOKEN}
EOF

# Lock down the env file — it contains proxy + DB + R2 creds. Default
# umask on some images is 0022, which would leave this world-readable.
chmod 600 "$ENV_FILE"

# ── Pull images and preflight while the old stack is still serving ────
cd "$DEPLOY_DIR"
start_maintenance_window

# Activate persistent Alloy state before any later deploy step can fail and
# run the rollback against the new Compose service spec. On the first rollout
# this migrates the live cursor and immediately recreates Alloy on the volume;
# later deploys take the no-restart fast path here.
prepare_alloy_state_volume
if (( ALLOY_STATE_ACTIVATION_REQUIRED )); then
  docker compose up -d --force-recreate alloy
fi

ensure_deploy_disk_headroom

pull_deploy_images
prepare_forward_data_snapshot

docker compose up -d redis

# ── Quiesce every local-Postgres writer before schema cutover ──────
# Migrations may introduce a database/runtime protocol (for example the
# shared-writer/oldest-writer-floor CDC boundary). Stop both sides before
# Alembic so no old process can write or advance a cursor in the interval
# between the schema change and the new containers starting. `--timeout 60`
# matches the app's 30s bounded drain with headroom before Docker sends
# SIGKILL. Redis and Alloy remain available throughout.
docker compose stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain

# ── Run Alembic migrations on local Postgres ─────────────────────────
docker run --rm \
  -e LOCAL_DATABASE_URL \
  -e CRAWLER_DB_ROLE=deploy-migrate \
  --network host \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-migrate \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync alembic -c src/migrations/alembic.ini upgrade head

# ── Patch Typesense schema (idempotent — adds new fields if missing) ─
# Must run BEFORE `crawler sync`, otherwise the next sync would upsert
# docs containing fields that the live schema doesn't know about.
docker run --rm \
  -e TYPESENSE_HOST \
  -e TYPESENSE_PORT \
  -e TYPESENSE_PROTOCOL \
  -e TYPESENSE_OPERATIONS_KEY \
  --network host \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-setup-typesense \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync crawler setup-typesense

# ── Sync board config from CSV → local Postgres + Redis + Typesense ──
FORWARD_SYNC_STARTED=1
docker run --rm \
  -e LOCAL_DATABASE_URL \
  -e WEB_DATABASE_URL \
  -e CRAWLER_DB_ROLE=deploy-sync \
  -e CRAWLER_DB_POOL_MIN=0 \
  -e CRAWLER_DB_POOL_MAX=4 \
  -e TYPESENSE_HOST \
  -e TYPESENSE_PORT \
  -e TYPESENSE_PROTOCOL \
  -e TYPESENSE_OPERATIONS_KEY \
  --network host \
  -v "$FORWARD_DATA_SNAPSHOT:/app/data:ro" \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-sync \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync crawler sync

# Revision 0021 is idempotent, but Alembic records it only once. If an earlier
# forward attempt reached the migration and then rolled back, the restored
# Teamtailor runtime could have re-admitted NW's legacy URLs. Reapply that exact
# bounded repair after the current WTTJ config is synced and before any worker
# can claim the board.
docker run --rm \
  -e LOCAL_DATABASE_URL \
  -e CRAWLER_DB_ROLE=deploy-nw-provider-cutover \
  -e CRAWLER_DB_POOL_MIN=0 \
  -e CRAWLER_DB_POOL_MAX=4 \
  --network host \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-nw-provider-cutover \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync crawler repair-nw-provider-cutover

# ── Start the full stack on the freshly seeded Redis state ───────────
# Coupled rollout marker (2026-08-04): this comment-only deploy contract
# change intentionally triggers the same-revision Murmur workflow and keeps
# the crawler workflow behind its Murmur safety wait for this one rollout.
docker compose up -d --remove-orphans

# Force-recreate alloy so it picks up any alloy.river bind-mount changes.
# Compose's plain ``up -d`` does not recreate a service when only the
# bind-mounted file's content changed — the service spec is unchanged
# from compose's perspective. Without this step, alloy keeps serving
# the previous config indefinitely (the container's bind-mount is
# pinned to the old inode rsync replaced). One extra ~2s alloy restart
# per deploy is well worth not having silent observability drift.
docker compose up -d --force-recreate alloy

# Gate success on the core crawler services actually running. The
# murmur shim is intentionally excluded while Murmur remains
# backburnered; a shim issue should not fail the crawler deploy.
wait_for_core_services
reconciliation_wrapper_is_compatible
verify_deployed_image_identity
stop_maintenance_window

# ── Cleanup ──────────────────────────────────────────────────────────
deploy_success_temporary="$(mktemp "${DEPLOY_DIR}/.crawler-deploy-success.XXXXXX")"
printf '%s\n' \
  "CRAWLER_IMAGE_TAG=$IMAGE_TAG" \
  "CRAWLER_IMAGE_REF=$CRAWLER_IMAGE_REF" \
  "BROWSER_IMAGE_REF=$BROWSER_IMAGE_REF" \
  "REDIS_IMAGE_REF=$REDIS_IMAGE" \
  "SHIM_IMAGE_REF=$SHIM_IMAGE_REF" \
  "ALLOY_IMAGE_REF=$ALLOY_IMAGE" \
  "JOBSEEK_DEPLOY_REVISION=$JOBSEEK_DEPLOY_REVISION" \
  "JOBSEEK_RUNTIME_CONTRACT_SHA256=$JOBSEEK_RUNTIME_CONTRACT_SHA256" \
  >"$deploy_success_temporary"
chmod 0644 "$deploy_success_temporary"
verify_shim_deploy_contract "$deploy_success_temporary"
publish_active_deploy_release \
  "$deploy_success_temporary" \
  "$FORWARD_DATA_SNAPSHOT" \
  "$FORWARD_DATA_FILES_MANIFEST"
# The active generation pointer is the one atomic commit for Compose, env, and
# success-marker state. Keep rollback armed through validation of that exact
# generation; a process crash before the pointer swap leaves the prior release
# selected, while a crash after it leaves the complete new release selected.
verify_shim_deploy_contract "$DEPLOY_SUCCESS_FILE"
# Keep the active generation, the immediate rollback target, any durable
# publication-journal references, and a bounded recent rollback window. This
# runs while the global mutation lock is still held, so no candidate generation
# can be pruned while another publisher is preparing it.
bash "$INCOMING_DIR/scripts/crawler-csv-sync-host.sh" \
  --prune-only "$ROLLBACK_ACTIVE_RELEASE_TARGET" "" "$FORWARD_DATA_STAGING_ROOT"
disarm_deploy_rollback
cleanup_forward_data_snapshot
rm -f "$deploy_success_temporary" "$ROLLBACK_ENV_FILE" "$ROLLBACK_SPEC_ARCHIVE" || true
publish_legacy_success_marker || {
  echo "WARNING: could not refresh deprecated crawler success-marker path" >&2
}
# Preserve the recent digest-pinned releases retained for delayed rollback.
# The emergency preflight above may prune unused images only when disk is
# already below the minimum required to deploy safely.
docker image prune -f || true
echo "Deploy complete: $(docker compose ps --format '{{.Name}}' | tr '\n' ' ')"

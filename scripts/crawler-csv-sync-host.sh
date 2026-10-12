#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR=${JOBSEEK_DEPLOY_DIR:-/home/deploy}
DEPLOY_ENV=${JOBSEEK_DEPLOY_ENV:-$DEPLOY_DIR/.env}
ACTIVE_RELEASE_POINTER=${JOBSEEK_ACTIVE_RELEASE_POINTER:-$DEPLOY_DIR/.crawler-active-release}
ACTIVE_RELEASE_ROOT=${JOBSEEK_ACTIVE_RELEASE_ROOT:-$DEPLOY_DIR/.crawler-release-generations}
PUBLICATION_JOURNAL=${JOBSEEK_PUBLICATION_JOURNAL:-$DEPLOY_DIR/.crawler-data-publication.env}
if [[ -e "$DEPLOY_DIR/.ordinary-go-owner-v1" || -L "$DEPLOY_DIR/.ordinary-go-owner-v1" ]]; then
  echo "ERROR: retire native ordinary ownership before publishing crawler data" >&2
  exit 1
fi
CANDIDATE_ROOT=${JOBSEEK_CANDIDATE_ROOT:-$DEPLOY_DIR/csv-candidates}
PUBLICATION_KEEP_GENERATIONS=${JOBSEEK_PUBLICATION_KEEP_GENERATIONS:-5}
PUBLICATION_GRACE_SECONDS=${JOBSEEK_PUBLICATION_GRACE_SECONDS:-172800}
OWNER=${JOBSEEK_OWNER:-colophon-group}
RUNTIME_ENV_ROOT=${JOBSEEK_RUNTIME_ENV_ROOT:-/run/lock}
CSV_NATIVE_TOOL_DIR=${JOBSEEK_CSV_NATIVE_TOOL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
CSV_NATIVE_TOOL_REVISION=${JOBSEEK_CSV_TOOL_REVISION:-${JOBSEEK_DEPLOY_REVISION:-}}
[[ -f "$CSV_NATIVE_TOOL_DIR/native-deploy-tools.sh" && ! -L "$CSV_NATIVE_TOOL_DIR/native-deploy-tools.sh" ]] || {
  echo "ERROR: native CSV deployment helper is unavailable or unsafe" >&2
  exit 1
}
source "$CSV_NATIVE_TOOL_DIR/native-deploy-tools.sh"
initialize_native_deploy_tools "$CSV_NATIVE_TOOL_DIR" "$CSV_NATIVE_TOOL_REVISION"
ACTIVE_RELEASE=""
ACTIVE_RELEASE_FORMAT=""
ACTIVE_DATA_DIR=""
ACTIVE_IMAGE_REF=""
ACTIVE_IMAGE_OVERRIDE=""
RUNTIME_ENV=""
NAME=""
PUBLICATION_ARMED=0
PREPARED_GENERATION=""
BOOTSTRAP_CANDIDATE_CLEANUP=0

read_exact_value() {
  local file="$1" key="$2"
  local -a values=()
  [[ -f "$file" && ! -L "$file" ]] || return 1
  mapfile -t values < <(sed -n "s/^${key}=//p" "$file")
  (( ${#values[@]} == 1 )) || return 1
  printf '%s\n' "${values[0]}"
}

verify_runtime_attestation_file() {
  local attestation="$1" previous_revision="$2" runtime_contract="$3"
  local compatible_revision="${4:-}"
  [[ -f "$attestation" && ! -L "$attestation" ]] || {
    echo "ERROR: previous-runtime attestation is unavailable or unsafe" >&2
    return 1
  }
  env -i "$NATIVE_DEPLOY_TOOLS" runtime-attestation \
    "$attestation" "$previous_revision" "$runtime_contract" "$compatible_revision"
}

verify_snapshot_file() {
  local snapshot="$1" digest_file="$2" label="$3" expected actual
  [[ -f "$snapshot" && ! -L "$snapshot" && -f "$digest_file" && ! -L "$digest_file" ]] || {
    echo "ERROR: committed crawler ${label} evidence is unavailable or unsafe" >&2
    return 1
  }
  expected="$(tr -d '[:space:]' <"$digest_file")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
  actual="$(sha256sum "$snapshot" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: committed crawler ${label} evidence failed verification" >&2
    return 1
  }
}

verify_exact_csv_tree() {
  local data_dir="$1" files_manifest="$2"
  [[ -d "$data_dir" && ! -L "$data_dir" && \
    -f "$files_manifest" && ! -L "$files_manifest" ]] || {
    echo "ERROR: committed crawler CSV snapshot is unavailable or unsafe" >&2
    return 1
  }
  env -i "$NATIVE_DEPLOY_TOOLS" csv-verify "$data_dir" "$files_manifest"
}

resolve_active_release() {
  local target
  [[ -d "$ACTIVE_RELEASE_ROOT" && ! -L "$ACTIVE_RELEASE_ROOT" && \
    -L "$ACTIVE_RELEASE_POINTER" ]] || {
    echo "ERROR: committed crawler release is unavailable or unsafe" >&2
    return 1
  }
  target="$(readlink "$ACTIVE_RELEASE_POINTER")"
  [[ "$target" == "$ACTIVE_RELEASE_ROOT/"* && \
    "${target#"$ACTIVE_RELEASE_ROOT/"}" =~ ^[A-Za-z0-9._-]+$ && \
    -d "$target" && ! -L "$target" ]] || {
    echo "ERROR: committed crawler release pointer is unsafe" >&2
    return 1
  }
  ACTIVE_RELEASE="$target"
}

verify_release_generation_evidence() (
  set -euo pipefail
  local generation="$1" format compose_digest env_digest success_digest
  local data_manifest_digest data_contract data_revision image_ref has_image_override
  local env_runtime success_runtime
  local -a image_override_digests=()
  [[ "$generation" == "$ACTIVE_RELEASE_ROOT/"* && \
    "${generation#"$ACTIVE_RELEASE_ROOT/"}" =~ ^[A-Za-z0-9._-]+$ && \
    -d "$generation" && ! -L "$generation" ]] || return 1
  verify_snapshot_file \
    "$generation/docker-compose.yml" "$generation/docker-compose.sha256" Compose || return 1
  verify_snapshot_file \
    "$generation/environment.env" "$generation/environment.sha256" environment || return 1
  [[ "$(stat -c '%a' "$generation/environment.env")" == 600 ]] || {
    echo "ERROR: committed crawler environment permissions are unsafe" >&2
    return 1
  }
  [[ -f "$generation/success.env" && ! -L "$generation/success.env" && \
    -f "$generation/release.manifest" && ! -L "$generation/release.manifest" ]] || return 1
  format="$(read_exact_value "$generation/release.manifest" RELEASE_FORMAT_VERSION)" || return 1
  [[ "$format" == 1 || "$format" == 2 || "$format" == 3 ]] || return 1
  compose_digest="$(read_exact_value "$generation/release.manifest" COMPOSE_SHA256)" || return 1
  env_digest="$(read_exact_value "$generation/release.manifest" ENVIRONMENT_SHA256)" || return 1
  success_digest="$(read_exact_value "$generation/release.manifest" SUCCESS_SHA256)" || return 1
  [[ "$compose_digest" =~ ^[0-9a-f]{64}$ && "$env_digest" =~ ^[0-9a-f]{64}$ && \
    "$success_digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ "$compose_digest" == "$(tr -d '[:space:]' <"$generation/docker-compose.sha256")" && \
    "$env_digest" == "$(tr -d '[:space:]' <"$generation/environment.sha256")" && \
    "$success_digest" == "$(sha256sum "$generation/success.env" | awk '{print $1}')" ]] || return 1
  image_ref="$(read_exact_value "$generation/environment.env" CRAWLER_IMAGE_REF)" || return 1
  [[ "$image_ref" =~ ^ghcr\.io/${OWNER}/jobseek-crawler@sha256:[0-9a-f]{64}$ ]] || return 1
  if [[ "$format" == 1 ]]; then
    [[ ! -e "$generation/rollback-images.override.yml" && \
      ! -L "$generation/rollback-images.override.yml" ]] || {
      echo "ERROR: format-1 crawler release contains unattested image-override residue" >&2
      return 1
    }
  elif [[ "$format" == 2 ]]; then
    local format2_override_digest bootstrap_legacy
    format2_override_digest="$(
      read_exact_value "$generation/release.manifest" IMAGE_OVERRIDE_SHA256
    )" || return 1
    bootstrap_legacy="$(
      read_exact_value "$generation/release.manifest" BOOTSTRAP_LEGACY
    )" || return 1
    [[ "$format2_override_digest" =~ ^[0-9a-f]{64}$ && \
      "$bootstrap_legacy" =~ ^[01]$ && \
      -f "$generation/rollback-images.override.yml" && \
      ! -L "$generation/rollback-images.override.yml" && \
      "$format2_override_digest" == "$(
        sha256sum "$generation/rollback-images.override.yml" | awk '{print $1}'
      )" ]] || {
      echo "ERROR: format-2 crawler release image override failed verification" >&2
      return 1
    }
  fi
  if [[ "$format" == 3 ]]; then
    data_manifest_digest="$(read_exact_value "$generation/release.manifest" DATA_FILES_SHA256)" || return 1
    data_contract="$(read_exact_value "$generation/release.manifest" DATA_CONTRACT_SHA256)" || return 1
    data_revision="$(read_exact_value "$generation/release.manifest" DATA_REVISION)" || return 1
    [[ "$data_manifest_digest" =~ ^[0-9a-f]{64}$ && \
      "$data_contract" =~ ^[0-9a-f]{64}$ && "$data_revision" =~ ^[0-9a-f]{40}$ ]] || return 1
    [[ "$data_manifest_digest" == "$(sha256sum "$generation/data-files.sha256" | awk '{print $1}')" ]] || {
      echo "ERROR: committed crawler CSV manifest failed verification" >&2
      return 1
    }
    [[ "$data_contract" == "$data_manifest_digest" ]] || {
      echo "ERROR: committed crawler data contract does not match its exact tree" >&2
      return 1
    }
    verify_exact_csv_tree "$generation/data" "$generation/data-files.sha256" || return 1
    env_runtime="$(read_exact_value "$generation/environment.env" JOBSEEK_RUNTIME_CONTRACT_SHA256)" || return 1
    success_runtime="$(read_exact_value "$generation/success.env" JOBSEEK_RUNTIME_CONTRACT_SHA256)" || return 1
    [[ "$env_runtime" =~ ^[0-9a-f]{64}$ && "$env_runtime" == "$success_runtime" ]] || {
      echo "ERROR: committed crawler runtime contract evidence is inconsistent" >&2
      return 1
    }
    has_image_override="$(read_exact_value "$generation/release.manifest" HAS_IMAGE_OVERRIDE)" || return 1
    mapfile -t image_override_digests < <(
      sed -n 's/^IMAGE_OVERRIDE_SHA256=//p' "$generation/release.manifest"
    )
    [[ "$has_image_override" == 0 || "$has_image_override" == 1 ]] || return 1
    if [[ "$has_image_override" == 1 ]]; then
      local override_digest
      (( ${#image_override_digests[@]} == 1 )) || return 1
      override_digest="${image_override_digests[0]}"
      [[ "$override_digest" =~ ^[0-9a-f]{64}$ && \
        -f "$generation/rollback-images.override.yml" && \
        ! -L "$generation/rollback-images.override.yml" && \
        "$override_digest" == "$(sha256sum "$generation/rollback-images.override.yml" | awk '{print $1}')" ]] || return 1
    else
      (( ${#image_override_digests[@]} == 0 )) || return 1
      [[ ! -e "$generation/rollback-images.override.yml" && \
        ! -L "$generation/rollback-images.override.yml" ]] || return 1
    fi
    env -i "$NATIVE_DEPLOY_TOOLS" release-bridge "$generation" "$OWNER" \
      >/dev/null || return 1
  fi
)

verify_release_generation() {
  local generation="$1" format image_ref has_image_override
  verify_release_generation_evidence "$generation" || return 1
  format="$(read_exact_value "$generation/release.manifest" RELEASE_FORMAT_VERSION)" || return 1
  image_ref="$(read_exact_value "$generation/environment.env" CRAWLER_IMAGE_REF)" || return 1
  ACTIVE_RELEASE_FORMAT="$format"
  ACTIVE_DATA_DIR=""
  ACTIVE_IMAGE_OVERRIDE=""
  if [[ "$format" == 3 ]]; then
    ACTIVE_DATA_DIR="$generation/data"
    has_image_override="$(
      read_exact_value "$generation/release.manifest" HAS_IMAGE_OVERRIDE
    )" || return 1
    if [[ "$has_image_override" == 1 ]]; then
      ACTIVE_IMAGE_OVERRIDE="$generation/rollback-images.override.yml"
    fi
  elif [[ "$format" == 2 ]]; then
    ACTIVE_IMAGE_OVERRIDE="$generation/rollback-images.override.yml"
  fi
  ACTIVE_IMAGE_REF="$image_ref"
}

load_committed_release() {
  local bridge_format
  resolve_active_release
  verify_release_generation "$ACTIVE_RELEASE"
  [[ -f "$DEPLOY_ENV" && ! -L "$DEPLOY_ENV" ]] || return 1
  if cmp -s \
    <(sed '/^COMPOSE_FILE=/d' "$DEPLOY_ENV") \
    <(sed '/^COMPOSE_FILE=/d' "$ACTIVE_RELEASE/environment.env")
  then
    return 0
  fi
  bridge_format="$(
    read_exact_value "$ACTIVE_RELEASE/release.manifest" LEGACY_BRIDGE_FORMAT_VERSION \
      2>/dev/null || true
  )"
  if [[ "$ACTIVE_RELEASE_FORMAT" == 3 && "$bridge_format" == 1 ]] && cmp -s \
    <(sed '/^COMPOSE_FILE=/d' "$DEPLOY_ENV") \
    <(sed -e '/^COMPOSE_FILE=/d' \
      -e '/^JOBSEEK_RUNTIME_CONTRACT_SHA256=/d' \
      "$ACTIVE_RELEASE/environment.env")
  then
    # A first-v3 bootstrap cannot rewrite the live env while the old stack is
    # running. The bridge manifest proves that the sole difference is the
    # attested runtime line; the enclosing full deploy immediately replaces or
    # rolls back the complete environment under the shared mutation lock.
    return 0
  fi
  echo "ERROR: live crawler environment drifted from committed release" >&2
  return 1
}

verify_runtime_contract() {
  local expected="$1" file mismatch=0
  local -a values=()
  load_committed_release
  for file in "$ACTIVE_RELEASE/environment.env" "$ACTIVE_RELEASE/success.env"; do
    mapfile -t values < <(sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$file")
    if (( ${#values[@]} == 0 )); then
      mismatch=1
    elif (( ${#values[@]} != 1 )) || [[ ! "${values[0]}" =~ ^[0-9a-f]{64}$ ]]; then
      echo "ERROR: committed crawler runtime contract is duplicated or invalid" >&2
      return 1
    elif [[ "${values[0]}" != "$expected" ]]; then
      mismatch=1
    fi
  done
  if (( mismatch )); then
    echo "WAIT: CSV config requires a crawler runtime that is not committed yet" >&2
    return 75
  fi
  if [[ "$ACTIVE_RELEASE_FORMAT" != 3 ]]; then
    echo "WAIT: exact committed CSV rollback evidence is not available yet" >&2
    return 75
  fi
}

active_data_contract_matches() {
  local expected="$1" committed
  [[ "$ACTIVE_RELEASE_FORMAT" == 3 ]] || return 1
  committed="$(read_exact_value "$ACTIVE_RELEASE/release.manifest" DATA_CONTRACT_SHA256)"
  [[ "$committed" == "$expected" ]]
}

prune_publications() {
  local protected_candidate="${1:-}" consumed_candidate="${2:-}"
  local protected_forward_staging="${3:-}" generation generation_basename format explicit_count
  local -a verified_v3_generations=() prune_args=()
  shift 3 || true
  explicit_count="$#"
  for generation in "$ACTIVE_RELEASE_ROOT"/*; do
    generation_basename="${generation#"$ACTIVE_RELEASE_ROOT/"}"
    [[ -d "$generation" && ! -L "$generation" && \
      "$generation_basename" =~ ^((release-|data-)[A-Za-z0-9._-]+|(murmur|legacy)\.[A-Za-z0-9._-]+)$ ]] || continue
    format="$(read_exact_value "$generation/release.manifest" RELEASE_FORMAT_VERSION 2>/dev/null || true)"
    [[ "$format" == 3 ]] || continue
    if verify_release_generation_evidence "$generation" >/dev/null 2>&1; then
      verified_v3_generations+=("$generation")
    fi
  done
  prune_args=(
    "$ACTIVE_RELEASE_ROOT" "$ACTIVE_RELEASE_POINTER" "$PUBLICATION_JOURNAL" \
    "$CANDIDATE_ROOT" "$DEPLOY_DIR" "$PUBLICATION_KEEP_GENERATIONS" \
    "$PUBLICATION_GRACE_SECONDS" "$protected_candidate" "$consumed_candidate" \
    "$protected_forward_staging" "$explicit_count" "$@"
  )
  if (( ${#verified_v3_generations[@]} )); then
    prune_args+=("${verified_v3_generations[@]}")
  fi
  env -i "$NATIVE_DEPLOY_TOOLS" publication-prune "${prune_args[@]}"
}

activate_release_generation() {
  local generation="$1"
  env -i "$NATIVE_DEPLOY_TOOLS" release-activate \
    "$generation" "$ACTIVE_RELEASE_POINTER" "$DEPLOY_DIR"
}

fsync_generation() {
  local generation="$1"
  env -i "$NATIVE_DEPLOY_TOOLS" release-fsync "$generation"
}

build_runtime_env() {
  local release="$1" key retained_web_database_url
  local -a required_env=(
    LOCAL_DATABASE_URL TYPESENSE_HOST TYPESENSE_PORT
    TYPESENSE_PROTOCOL TYPESENSE_OPERATIONS_KEY
  ) matches=() retained_web_database_urls=()
  # Legacy release snapshots may retain the web-owned credential because their
  # sync implementation still opens that database. Current release snapshots
  # omit it, so ordinary post-cutover syncs remain credential-free.
  mapfile -t retained_web_database_urls < <(
    sed -n 's/^WEB_DATABASE_URL=//p' "$release/environment.env"
  )
  if (( ${#retained_web_database_urls[@]} > 1 )); then
    echo "ERROR: retained web database credential is duplicated for CSV sync" >&2
    return 1
  fi
  if (( ${#retained_web_database_urls[@]} == 1 )); then
    retained_web_database_url="${retained_web_database_urls[0]}"
    [[ -n "$retained_web_database_url" ]] || {
      echo "ERROR: retained web database credential is empty for CSV sync" >&2
      return 1
    }
  fi
  RUNTIME_ENV="$(mktemp "${RUNTIME_ENV_ROOT}/jobseek-csv-sync-env.XXXXXX")"
  chmod 0600 "$RUNTIME_ENV"
  for key in "${required_env[@]}"; do
    mapfile -t matches < <(grep -E "^${key}=" "$release/environment.env" || true)
    if (( ${#matches[@]} != 1 )) || [[ -z "${matches[0]#*=}" ]]; then
      echo "ERROR: committed CSV sync variable ${key} is missing or duplicated" >&2
      return 1
    fi
    printf '%s\n' "${matches[0]}" >>"$RUNTIME_ENV"
  done
  if (( ${#retained_web_database_urls[@]} == 1 )); then
    printf 'WEB_DATABASE_URL=%s\n' "$retained_web_database_url" >>"$RUNTIME_ENV"
  fi
  printf '%s\n' \
    'CRAWLER_DB_ROLE=csv-sync' \
    'CRAWLER_DB_POOL_MIN=0' \
    'CRAWLER_DB_POOL_MAX=4' \
    'CRAWLER_DB_POOL_IDLE_SECONDS=60' >>"$RUNTIME_ENV"
}

sync_release_data() {
  local release="$1" role="${2:-csv-sync}" image data_args=()
  verify_release_generation "$release"
  image="$ACTIVE_IMAGE_REF"
  build_runtime_env "$release"
  if [[ "$ACTIVE_RELEASE_FORMAT" == 3 ]]; then
    data_args=(-v "$ACTIVE_DATA_DIR:/app/data:ro")
  fi
  NAME="crawler-csv-data-sync-${BASHPID}"
  # Normal publication has passed the exact runtime-contract gate and uses
  # the installed Go synchronizer directly. Bootstrap/recovery may select a
  # historical image whose supported entrypoint is still the Python CLI.
  local command_args=(uv run --no-sync crawler sync)
  if [[ "$role" == csv-sync ]]; then
    command_args=(go-typesense-exporter --sync-registry)
  fi
  docker run --rm --name "$NAME" --env-file "$RUNTIME_ENV" --network host \
    "${data_args[@]}" -e "CRAWLER_DB_ROLE=$role" "$image" \
    "${command_args[@]}"
  rm -f "$RUNTIME_ENV"
  RUNTIME_ENV=""
  NAME=""
}

write_journal() {
  local state="$1" previous="$2" target="$3" action="${4:-restore-previous}" temporary
  [[ "$action" == restore-previous || "$action" == promote-target ]] || return 1
  temporary="$(mktemp "${PUBLICATION_JOURNAL}.XXXXXX")"
  printf '%s\n' \
    'PUBLICATION_FORMAT_VERSION=1' \
    "SYNC_SUCCEEDED=$state" \
    "RECOVERY_ACTION=$action" \
    "PREVIOUS_RELEASE=$previous" \
    "TARGET_RELEASE=$target" >"$temporary"
  chmod 0600 "$temporary"
  env -i "$NATIVE_DEPLOY_TOOLS" journal-publish \
    "$temporary" "$PUBLICATION_JOURNAL" "$DEPLOY_DIR"
}

clear_journal() {
  rm -f -- "$PUBLICATION_JOURNAL"
  env -i "$NATIVE_DEPLOY_TOOLS" directory-fsync "$DEPLOY_DIR"
}

recover_publication() (
  set -euo pipefail
  local state action previous target selected
  [[ -e "$PUBLICATION_JOURNAL" ]] || return 0
  [[ -f "$PUBLICATION_JOURNAL" && ! -L "$PUBLICATION_JOURNAL" && \
    "$(stat -c '%a' "$PUBLICATION_JOURNAL")" == 600 ]] || return 1
  [[ "$(read_exact_value "$PUBLICATION_JOURNAL" PUBLICATION_FORMAT_VERSION)" == 1 ]] || return 1
  state="$(read_exact_value "$PUBLICATION_JOURNAL" SYNC_SUCCEEDED)"
  action="$(read_exact_value "$PUBLICATION_JOURNAL" RECOVERY_ACTION)"
  previous="$(read_exact_value "$PUBLICATION_JOURNAL" PREVIOUS_RELEASE)"
  target="$(read_exact_value "$PUBLICATION_JOURNAL" TARGET_RELEASE)"
  [[ "$state" == 0 || "$state" == 1 ]] || return 1
  [[ "$action" == restore-previous || "$action" == promote-target ]] || return 1
  verify_release_generation "$previous"
  verify_release_generation "$target"
  resolve_active_release
  selected="$ACTIVE_RELEASE"
  if [[ "$state" == 1 ]]; then
    [[ "$selected" == "$previous" || "$selected" == "$target" ]] || return 1
    activate_release_generation "$target"
  elif [[ "$action" == promote-target ]]; then
    [[ "$selected" == "$previous" ]] || return 1
    echo "Recovering legacy CSV bootstrap from exact staged data" >&2
    sync_release_data "$target" recovery-sync
    write_journal 1 "$previous" "$target" "$action"
    activate_release_generation "$target"
  else
    [[ "$selected" == "$previous" ]] || return 1
    echo "Recovering interrupted CSV publication by restoring prior committed data" >&2
    sync_release_data "$previous" recovery-sync
  fi
  clear_journal
)

verify_candidate_archive_contract() {
  local revision="$1" data_contract="$2" candidate_id="$3" archive_sha="$4"
  local runtime_contract="${5:-}"
  local candidate archive
  candidate="$CANDIDATE_ROOT/$candidate_id"
  archive="$candidate/csv-snapshot.tar"
  [[ "$candidate_id" =~ ^${revision}-[1-9][0-9]*-[1-9][0-9]*$ && \
    -d "$candidate" && ! -L "$candidate" && -f "$archive" && ! -L "$archive" ]] || {
    echo "ERROR: revision-scoped CSV candidate is unavailable or unsafe" >&2
    return 1
  }
  [[ "$(sha256sum "$archive" | awk '{print $1}')" == "$archive_sha" ]] || {
    echo "ERROR: CSV candidate archive digest mismatch" >&2
    return 1
  }
  env -i "$NATIVE_DEPLOY_TOOLS" csv-archive-verify \
    "$archive" "$data_contract" "$revision" "$runtime_contract"
}

verify_legacy_runtime_evidence() {
  local previous_revision="$1" runtime_contract="$2" attestation="$3"
  local source_revision source_format bootstrap_legacy image_override_digest
  local configured_images_output configured_image key env_value success_value expected_ref
  local -a compose_args=() configured_images=() runtime_env_values=() runtime_success_values=()
  [[ "$ACTIVE_RELEASE_FORMAT" == 1 || "$ACTIVE_RELEASE_FORMAT" == 2 ]] || return 1
  source_format="$ACTIVE_RELEASE_FORMAT"
  source_revision="$(
    read_exact_value "$ACTIVE_RELEASE/environment.env" JOBSEEK_DEPLOY_REVISION
  )" || return 1
  [[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || return 1
  verify_runtime_attestation_file \
    "$attestation" "$previous_revision" "$runtime_contract" "$source_revision" || return 1

  for key in \
    CRAWLER_IMAGE_TAG CRAWLER_IMAGE_REF BROWSER_IMAGE_REF SHIM_IMAGE_REF \
    JOBSEEK_DEPLOY_REVISION
  do
    env_value="$(read_exact_value "$ACTIVE_RELEASE/environment.env" "$key")" || return 1
    success_value="$(read_exact_value "$ACTIVE_RELEASE/success.env" "$key")" || return 1
    [[ -n "$env_value" && "$env_value" == "$success_value" ]] || {
      echo "ERROR: legacy release marker and environment disagree on ${key}" >&2
      return 1
    }
  done
  [[ "$(read_exact_value "$ACTIVE_RELEASE/environment.env" CRAWLER_IMAGE_REF)" \
      =~ ^ghcr\.io/${OWNER}/jobseek-crawler@sha256:[0-9a-f]{64}$ && \
    "$(read_exact_value "$ACTIVE_RELEASE/environment.env" BROWSER_IMAGE_REF)" \
      =~ ^ghcr\.io/${OWNER}/jobseek-crawler-browser@sha256:[0-9a-f]{64}$ && \
    "$(read_exact_value "$ACTIVE_RELEASE/environment.env" SHIM_IMAGE_REF)" \
      =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: legacy release image identity is not immutable" >&2
    return 1
  }

  mapfile -t runtime_env_values < <(
    sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$ACTIVE_RELEASE/environment.env"
  )
  mapfile -t runtime_success_values < <(
    sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$ACTIVE_RELEASE/success.env"
  )
  if (( ${#runtime_env_values[@]} || ${#runtime_success_values[@]} )); then
    (( ${#runtime_env_values[@]} == 1 && ${#runtime_success_values[@]} == 1 )) && \
      [[ "${runtime_env_values[0]}" == "$runtime_contract" && \
        "${runtime_success_values[0]}" == "$runtime_contract" ]] || {
      echo "ERROR: legacy release runtime evidence is partial or mismatched" >&2
      return 1
    }
  fi

  compose_args=(-f "$ACTIVE_RELEASE/docker-compose.yml")
  if [[ "$source_format" == 2 ]]; then
    bootstrap_legacy="$(
      read_exact_value "$ACTIVE_RELEASE/release.manifest" BOOTSTRAP_LEGACY
    )" || return 1
    image_override_digest="$(
      read_exact_value "$ACTIVE_RELEASE/release.manifest" IMAGE_OVERRIDE_SHA256
    )" || return 1
    [[ "$bootstrap_legacy" == 0 || "$bootstrap_legacy" == 1 ]] || return 1
    [[ "$image_override_digest" =~ ^[0-9a-f]{64}$ && \
      "$ACTIVE_IMAGE_OVERRIDE" == "$ACTIVE_RELEASE/rollback-images.override.yml" && \
      -f "$ACTIVE_IMAGE_OVERRIDE" && ! -L "$ACTIVE_IMAGE_OVERRIDE" && \
      "$image_override_digest" == "$(
        sha256sum "$ACTIVE_IMAGE_OVERRIDE" | awk '{print $1}'
      )" ]] || {
      echo "ERROR: legacy release image override failed verification" >&2
      return 1
    }
    compose_args+=(-f "$ACTIVE_IMAGE_OVERRIDE")
  else
    [[ -z "$ACTIVE_IMAGE_OVERRIDE" && \
      ! -e "$ACTIVE_RELEASE/rollback-images.override.yml" && \
      ! -L "$ACTIVE_RELEASE/rollback-images.override.yml" ]] || return 1
  fi
  configured_images_output="$(
    env -i \
      "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}" \
      "HOME=${HOME:-$DEPLOY_DIR}" \
      COMPOSE_PROJECT_NAME=deploy \
      docker compose --env-file "$ACTIVE_RELEASE/environment.env" \
        "${compose_args[@]}" config --images
  )" || return 1
  [[ -n "$configured_images_output" ]] || return 1
  mapfile -t configured_images <<<"$configured_images_output"
  for configured_image in "${configured_images[@]}"; do
    [[ "$configured_image" =~ @sha256:[0-9a-f]{64}$ ]] || {
      echo "ERROR: legacy release Compose resolves a mutable image" >&2
      return 1
    }
  done
  for key in CRAWLER_IMAGE_REF BROWSER_IMAGE_REF SHIM_IMAGE_REF; do
    expected_ref="$(read_exact_value "$ACTIVE_RELEASE/environment.env" "$key")" || return 1
    printf '%s\n' "${configured_images[@]}" | grep -Fqx -- "$expected_ref" || {
      echo "ERROR: legacy release Compose does not bind ${key}" >&2
      return 1
    }
  done
}

prepare_candidate_generation() {
  local revision="$1" data_contract="$2" candidate_id="$3" archive_sha="$4"
  local runtime_contract="${5:-}"
  local candidate archive generation="" compose_digest env_digest success_digest files_digest
  local attestation_temp="" verification_status=0 extraction_status=0
  local source_format source_revision source_image_ref source_compose_digest
  local source_env_digest source_success_digest attestation_digest bridge_required=0
  local -a runtime_env_values=() runtime_success_values=()
  verify_candidate_archive_contract \
    "$revision" "$data_contract" "$candidate_id" "$archive_sha" "$runtime_contract" || return 1
  candidate="$CANDIDATE_ROOT/$candidate_id"
  archive="$candidate/csv-snapshot.tar"
  if [[ -n "$runtime_contract" && "$ACTIVE_RELEASE_FORMAT" != 3 ]]; then
    attestation_temp="$(mktemp "${candidate}/runtime-attestation.XXXXXX")" || return 1
    if ! tar -xOf "$archive" runtime-attestation.env >"$attestation_temp"; then
      rm -f -- "$attestation_temp"
      return 1
    fi
    chmod 0644 "$attestation_temp" || {
      rm -f -- "$attestation_temp"
      return 1
    }
    verify_legacy_runtime_evidence \
      "$revision" "$runtime_contract" "$attestation_temp" || verification_status=$?
    rm -f -- "$attestation_temp"
    (( verification_status == 0 )) || return "$verification_status"
    mapfile -t runtime_env_values < <(
      sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$ACTIVE_RELEASE/environment.env"
    )
    mapfile -t runtime_success_values < <(
      sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$ACTIVE_RELEASE/success.env"
    )
    if (( ${#runtime_env_values[@]} == 0 && ${#runtime_success_values[@]} == 0 )); then
      bridge_required=1
    fi
  fi
  generation="$(mktemp -d "${ACTIVE_RELEASE_ROOT}/data-${revision}.XXXXXX")" || return 1
  PREPARED_GENERATION="$generation"
  trap 'status=$?; if (( status != 0 )) && [[ -n "$PREPARED_GENERATION" && -d "$PREPARED_GENERATION" ]]; then discard_candidate_generation "$PREPARED_GENERATION" || status=1; fi; exit "$status"' EXIT
  install -m 0644 "$ACTIVE_RELEASE/docker-compose.yml" \
    "$generation/docker-compose.yml" || return 1
  install -m 0600 "$ACTIVE_RELEASE/environment.env" \
    "$generation/environment.env" || return 1
  install -m 0644 "$ACTIVE_RELEASE/success.env" \
    "$generation/success.env" || return 1
  if [[ -n "$ACTIVE_IMAGE_OVERRIDE" ]]; then
    install -m 0644 "$ACTIVE_IMAGE_OVERRIDE" \
      "$generation/rollback-images.override.yml" || return 1
  fi
  if (( bridge_required )); then
    install -m 0644 "$ACTIVE_RELEASE/docker-compose.yml" \
      "$generation/legacy-source-compose.yml" || return 1
    install -m 0600 "$ACTIVE_RELEASE/environment.env" \
      "$generation/legacy-source-environment.env" || return 1
    install -m 0644 "$ACTIVE_RELEASE/success.env" \
      "$generation/legacy-source-success.env" || return 1
    if [[ "$ACTIVE_RELEASE_FORMAT" == 2 ]]; then
      install -m 0644 "$ACTIVE_IMAGE_OVERRIDE" \
        "$generation/legacy-source-images.override.yml" || return 1
    fi
    [[ "$(tail -c 1 "$generation/environment.env" | od -An -t x1 | tr -d '[:space:]')" == 0a && \
      "$(tail -c 1 "$generation/success.env" | od -An -t x1 | tr -d '[:space:]')" == 0a ]] || {
      echo "ERROR: legacy runtime snapshots are not canonically newline-terminated" >&2
      return 1
    }
    printf 'JOBSEEK_RUNTIME_CONTRACT_SHA256=%s\n' "$runtime_contract" \
      >>"$generation/environment.env" || return 1
    printf 'JOBSEEK_RUNTIME_CONTRACT_SHA256=%s\n' "$runtime_contract" \
      >>"$generation/success.env" || return 1
  fi
  env -i "$NATIVE_DEPLOY_TOOLS" csv-unpack \
    "$archive" "$generation" "$data_contract" "$revision" "$runtime_contract" \
    || extraction_status=$?
  (( extraction_status == 0 )) || return "$extraction_status"
  if (( ! bridge_required )); then
    rm -f -- "$generation/runtime-attestation.env" || return 1
  fi
  [[ "$(sha256sum "$archive" | awk '{print $1}')" == "$archive_sha" ]] || {
    echo "ERROR: CSV candidate archive changed during preparation" >&2
    return 1
  }
  verify_exact_csv_tree "$generation/data" "$generation/data-files.sha256" || return 1
  compose_digest="$(sha256sum "$generation/docker-compose.yml" | awk '{print $1}')" || return 1
  env_digest="$(sha256sum "$generation/environment.env" | awk '{print $1}')" || return 1
  success_digest="$(sha256sum "$generation/success.env" | awk '{print $1}')" || return 1
  files_digest="$(sha256sum "$generation/data-files.sha256" | awk '{print $1}')" || return 1
  [[ "$files_digest" == "$data_contract" ]] || {
    echo "ERROR: CSV candidate data contract changed during preparation" >&2
    return 1
  }
  printf '%s\n' "$compose_digest" >"$generation/docker-compose.sha256" || return 1
  printf '%s\n' "$env_digest" >"$generation/environment.sha256" || return 1
  printf '%s\n' \
    'RELEASE_FORMAT_VERSION=3' \
    "COMPOSE_SHA256=$compose_digest" \
    "ENVIRONMENT_SHA256=$env_digest" \
    "SUCCESS_SHA256=$success_digest" \
    "DATA_FILES_SHA256=$files_digest" \
    "DATA_CONTRACT_SHA256=$data_contract" \
    "DATA_REVISION=$revision" \
    >"$generation/release.manifest" || return 1
  if [[ -f "$generation/rollback-images.override.yml" ]]; then
    printf '%s\n' \
      'HAS_IMAGE_OVERRIDE=1' \
      "IMAGE_OVERRIDE_SHA256=$(sha256sum "$generation/rollback-images.override.yml" | awk '{print $1}')" \
      'BOOTSTRAP_LEGACY=0' >>"$generation/release.manifest" || return 1
  else
    printf '%s\n' 'HAS_IMAGE_OVERRIDE=0' >>"$generation/release.manifest" || return 1
  fi
  if (( bridge_required )); then
    source_format="$ACTIVE_RELEASE_FORMAT"
    source_revision="$(
      read_exact_value "$ACTIVE_RELEASE/environment.env" JOBSEEK_DEPLOY_REVISION
    )" || return 1
    source_image_ref="$(
      read_exact_value "$ACTIVE_RELEASE/environment.env" CRAWLER_IMAGE_REF
    )" || return 1
    source_compose_digest="$(tr -d '[:space:]' <"$ACTIVE_RELEASE/docker-compose.sha256")" || return 1
    source_env_digest="$(tr -d '[:space:]' <"$ACTIVE_RELEASE/environment.sha256")" || return 1
    source_success_digest="$(sha256sum "$ACTIVE_RELEASE/success.env" | awk '{print $1}')" || return 1
    attestation_digest="$(sha256sum "$generation/runtime-attestation.env" | awk '{print $1}')" || return 1
    printf '%s\n' \
      'LEGACY_BRIDGE_FORMAT_VERSION=1' \
      'LEGACY_BRIDGE_TRANSITIVE=0' \
      "LEGACY_RUNTIME_ATTESTATION_SHA256=$attestation_digest" \
      "LEGACY_SOURCE_RELEASE_FORMAT=$source_format" \
      "LEGACY_SOURCE_REVISION=$source_revision" \
      "LEGACY_SOURCE_CRAWLER_IMAGE_REF=$source_image_ref" \
      "LEGACY_SOURCE_COMPOSE_SHA256=$source_compose_digest" \
      "LEGACY_SOURCE_ENVIRONMENT_SHA256=$source_env_digest" \
      "LEGACY_SOURCE_SUCCESS_SHA256=$source_success_digest" \
      >>"$generation/release.manifest" || return 1
    if [[ "$source_format" == 2 ]]; then
      printf 'LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256=%s\n' \
        "$(sha256sum "$generation/legacy-source-images.override.yml" | awk '{print $1}')" \
        >>"$generation/release.manifest" || return 1
    fi
  fi
  chmod 0644 "$generation"/*.sha256 "$generation/release.manifest" || return 1
  fsync_generation "$generation" || return 1
  verify_release_generation "$generation" || return 1
  PREPARED_GENERATION=""
  trap - EXIT
  printf '%s\n' "$generation"
}

discard_candidate_generation() {
  local generation="$1"
  [[ "$generation" == "$ACTIVE_RELEASE_ROOT/data-${REVISION}."* && \
    "${generation#"$ACTIVE_RELEASE_ROOT/"}" =~ ^data-${REVISION}\.[A-Za-z0-9]+$ && \
    -d "$generation" && ! -L "$generation" ]] || return 1
  resolve_active_release
  [[ "$generation" != "$ACTIVE_RELEASE" ]] || return 1
  rm -rf -- "$generation"
  env -i "$NATIVE_DEPLOY_TOOLS" directory-fsync "$ACTIVE_RELEASE_ROOT"
}

discard_bootstrap_candidate() {
  local candidate="$CANDIDATE_ROOT/$CANDIDATE_ID"
  [[ "$CANDIDATE_ID" =~ ^${REVISION}-[1-9][0-9]*-[1-9][0-9]*$ ]] || return 1
  if [[ ! -e "$candidate" && ! -L "$candidate" ]]; then
    return 0
  fi
  [[ -d "$CANDIDATE_ROOT" && ! -L "$CANDIDATE_ROOT" && \
    -d "$candidate" && ! -L "$candidate" ]] || return 1
  rm -rf -- "$candidate"
  env -i "$NATIVE_DEPLOY_TOOLS" directory-fsync "$CANDIDATE_ROOT"
}

cleanup() {
  local status="$?"
  trap - EXIT HUP INT TERM
  set +e
  [[ -z "$NAME" ]] || docker rm -f "$NAME" >/dev/null 2>&1
  [[ -z "$RUNTIME_ENV" ]] || rm -f "$RUNTIME_ENV"
  if (( PUBLICATION_ARMED )); then
    recover_publication || {
      echo "ERROR: interrupted CSV publication requires recovery before later mutation" >&2
      status=1
    }
  fi
  if (( BOOTSTRAP_CANDIDATE_CLEANUP )); then
    discard_bootstrap_candidate || {
      echo "ERROR: failed to remove rejected bootstrap candidate" >&2
      status=1
    }
  fi
  exit "$status"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "${1:-}" == --recover-only ]]; then
  recover_publication
  prune_publications "" "" ""
  exit 0
fi

if [[ "${1:-}" == --prune-only ]]; then
  protected_release="${2:-}"
  protected_candidate="${3:-}"
  protected_forward_staging="${4:-}"
  [[ -z "${5:-}" ]] || {
    echo "ERROR: usage: crawler-csv-sync-host.sh --prune-only [release] [candidate] [forward-staging]" >&2
    exit 1
  }
  prune_publications \
    "$protected_candidate" "" "$protected_forward_staging" "$protected_release"
  exit 0
fi

if [[ "${1:-}" == --bootstrap-current ]]; then
  REVISION="${2:?bootstrap revision is required}"
  PREVIOUS_RUNTIME_CONTRACT_SHA256="${3:?bootstrap previous runtime contract is required}"
  DATA_CONTRACT_SHA256="${4:?bootstrap data contract is required}"
  CANDIDATE_ID="${5:?bootstrap candidate ID is required}"
  ARCHIVE_SHA256="${6:?bootstrap archive digest is required}"
  [[ -z "${7:-}" ]] || {
    echo "ERROR: usage: crawler-csv-sync-host.sh --bootstrap-current <revision> <previous-runtime-contract> <data-contract> <candidate-id> <archive-sha256>" >&2
    exit 1
  }
  [[ "$REVISION" =~ ^[a-f0-9]{40}$ && \
    "$PREVIOUS_RUNTIME_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ && \
    "$DATA_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ && \
    "$ARCHIVE_SHA256" =~ ^[a-f0-9]{64}$ && \
    "$CANDIDATE_ID" =~ ^${REVISION}-[1-9][0-9]*-[1-9][0-9]*$ ]] || exit 1
  BOOTSTRAP_CANDIDATE_CLEANUP=1
  recover_publication
  verify_candidate_archive_contract \
    "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256"
  load_committed_release
  if [[ "$ACTIVE_RELEASE_FORMAT" == 3 ]]; then
    # A verified v3 generation is the exact live rollback source of truth. A
    # later main revision may contain different CSVs; replacing this evidence
    # before the new deploy commits would make rollback non-atomic.
    prune_publications "" "$CANDIDATE_ID" "" "$ACTIVE_RELEASE"
    BOOTSTRAP_CANDIDATE_CLEANUP=0
    exit 0
  fi
  previous_release="$ACTIVE_RELEASE"
  verify_candidate_archive_contract \
    "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256" \
    "$PREVIOUS_RUNTIME_CONTRACT_SHA256"
  candidate_generation="$(
    prepare_candidate_generation \
      "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256" \
      "$PREVIOUS_RUNTIME_CONTRACT_SHA256"
  )"
  write_journal 0 "$previous_release" "$candidate_generation" promote-target
  PUBLICATION_ARMED=1
  prune_publications \
    "" "$CANDIDATE_ID" "" "$previous_release" "$candidate_generation"
  sync_release_data "$candidate_generation" bootstrap-sync
  write_journal 1 "$previous_release" "$candidate_generation" promote-target
  activate_release_generation "$candidate_generation"
  resolve_active_release
  [[ "$ACTIVE_RELEASE" == "$candidate_generation" ]]
  verify_release_generation "$ACTIVE_RELEASE"
  clear_journal
  PUBLICATION_ARMED=0
  prune_publications "" "" "" "$previous_release" "$candidate_generation"
  BOOTSTRAP_CANDIDATE_CLEANUP=0
  exit 0
fi

USAGE="crawler-csv-sync-host.sh <revision> <runtime-contract> <data-contract> <candidate-id> <archive-sha256> [--check-runtime]"
REVISION="${1:?usage: $USAGE}"
RUNTIME_CONTRACT_SHA256="${2:?usage: $USAGE}"
DATA_CONTRACT_SHA256="${3:?usage: $USAGE}"
CANDIDATE_ID="${4:?usage: $USAGE}"
ARCHIVE_SHA256="${5:?usage: $USAGE}"
MODE="${6:-}"
[[ "$REVISION" =~ ^[a-f0-9]{40}$ && "$RUNTIME_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ && \
  "$DATA_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ && "$ARCHIVE_SHA256" =~ ^[a-f0-9]{64}$ ]] || {
  echo "ERROR: invalid CSV publication identity" >&2
  exit 1
}
[[ -z "$MODE" || "$MODE" == --check-runtime ]] || {
  echo "ERROR: usage: $USAGE" >&2
  exit 1
}

if [[ -e "$PUBLICATION_JOURNAL" ]]; then
  if [[ "$MODE" == --check-runtime ]]; then
    echo "Interrupted CSV publication will be recovered under the mutation lock" >&2
    exit 0
  fi
  recover_publication
fi
if [[ "$MODE" == --check-runtime ]]; then
  verify_candidate_archive_contract \
    "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256"
  load_committed_release
  if active_data_contract_matches "$DATA_CONTRACT_SHA256"; then
    echo "CSV data contract is already committed by the active crawler generation" >&2
    exit 0
  fi
  verify_runtime_contract "$RUNTIME_CONTRACT_SHA256"
  exit 0
fi

load_committed_release
prune_publications "$CANDIDATE_ID" "" "" "$ACTIVE_RELEASE"
candidate_generation="$(
  prepare_candidate_generation \
    "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256"
)"
if active_data_contract_matches "$DATA_CONTRACT_SHA256"; then
  echo "CSV data contract is already committed by the active crawler generation" >&2
  discard_candidate_generation "$candidate_generation"
  prune_publications "" "$CANDIDATE_ID" "" "$ACTIVE_RELEASE"
  exit 0
fi
verify_runtime_contract "$RUNTIME_CONTRACT_SHA256"
previous_release="$ACTIVE_RELEASE"
write_journal 0 "$previous_release" "$candidate_generation" restore-previous
PUBLICATION_ARMED=1
prune_publications \
  "" "$CANDIDATE_ID" "" "$previous_release" "$candidate_generation"
sync_release_data "$candidate_generation" csv-sync
write_journal 1 "$previous_release" "$candidate_generation" restore-previous
activate_release_generation "$candidate_generation"
resolve_active_release
[[ "$ACTIVE_RELEASE" == "$candidate_generation" ]]
verify_release_generation "$ACTIVE_RELEASE"
clear_journal
PUBLICATION_ARMED=0
prune_publications "" "" "" "$previous_release" "$candidate_generation"

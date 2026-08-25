#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR=${JOBSEEK_DEPLOY_DIR:-/home/deploy}
DEPLOY_ENV=${JOBSEEK_DEPLOY_ENV:-$DEPLOY_DIR/.env}
ACTIVE_RELEASE_POINTER=${JOBSEEK_ACTIVE_RELEASE_POINTER:-$DEPLOY_DIR/.crawler-active-release}
ACTIVE_RELEASE_ROOT=${JOBSEEK_ACTIVE_RELEASE_ROOT:-$DEPLOY_DIR/.crawler-release-generations}
PUBLICATION_JOURNAL=${JOBSEEK_PUBLICATION_JOURNAL:-$DEPLOY_DIR/.crawler-data-publication.env}
CANDIDATE_ROOT=${JOBSEEK_CANDIDATE_ROOT:-$DEPLOY_DIR/csv-candidates}
PUBLICATION_KEEP_GENERATIONS=${JOBSEEK_PUBLICATION_KEEP_GENERATIONS:-5}
PUBLICATION_GRACE_SECONDS=${JOBSEEK_PUBLICATION_GRACE_SECONDS:-172800}
OWNER=${JOBSEEK_OWNER:-colophon-group}
ACTIVE_RELEASE=""
ACTIVE_RELEASE_FORMAT=""
ACTIVE_DATA_DIR=""
ACTIVE_IMAGE_REF=""
RUNTIME_ENV=""
NAME=""
PUBLICATION_ARMED=0

read_exact_value() {
  local file="$1" key="$2"
  local -a values=()
  [[ -f "$file" && ! -L "$file" ]] || return 1
  mapfile -t values < <(sed -n "s/^${key}=//p" "$file")
  (( ${#values[@]} == 1 )) || return 1
  printf '%s\n' "${values[0]}"
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
    [[ "$has_image_override" == 0 || "$has_image_override" == 1 ]] || return 1
    if [[ "$has_image_override" == 1 ]]; then
      local override_digest
      override_digest="$(read_exact_value "$generation/release.manifest" IMAGE_OVERRIDE_SHA256)" || return 1
      [[ "$override_digest" =~ ^[0-9a-f]{64}$ && \
        -f "$generation/rollback-images.override.yml" && \
        ! -L "$generation/rollback-images.override.yml" && \
        "$override_digest" == "$(sha256sum "$generation/rollback-images.override.yml" | awk '{print $1}')" ]] || return 1
    else
      [[ ! -e "$generation/rollback-images.override.yml" ]] || return 1
    fi
  fi
)

verify_release_generation() {
  local generation="$1" format image_ref
  verify_release_generation_evidence "$generation" || return 1
  format="$(read_exact_value "$generation/release.manifest" RELEASE_FORMAT_VERSION)" || return 1
  image_ref="$(read_exact_value "$generation/environment.env" CRAWLER_IMAGE_REF)" || return 1
  ACTIVE_RELEASE_FORMAT="$format"
  ACTIVE_DATA_DIR=""
  if [[ "$format" == 3 ]]; then
    ACTIVE_DATA_DIR="$generation/data"
  fi
  ACTIVE_IMAGE_REF="$image_ref"
}

load_committed_release() {
  resolve_active_release
  verify_release_generation "$ACTIVE_RELEASE"
  [[ -f "$DEPLOY_ENV" && ! -L "$DEPLOY_ENV" ]] || return 1
  cmp -s \
    <(sed '/^COMPOSE_FILE=/d' "$DEPLOY_ENV") \
    <(sed '/^COMPOSE_FILE=/d' "$ACTIVE_RELEASE/environment.env") || {
    echo "ERROR: live crawler environment drifted from committed release" >&2
    return 1
  }
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
  local -a verified_v3_generations=() python_args=()
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
  python_args=(
    "$ACTIVE_RELEASE_ROOT" "$ACTIVE_RELEASE_POINTER" "$PUBLICATION_JOURNAL" \
    "$CANDIDATE_ROOT" "$DEPLOY_DIR" "$PUBLICATION_KEEP_GENERATIONS" \
    "$PUBLICATION_GRACE_SECONDS" "$protected_candidate" "$consumed_candidate" \
    "$protected_forward_staging" "$explicit_count" "$@"
  )
  if (( ${#verified_v3_generations[@]} )); then
    python_args+=("${verified_v3_generations[@]}")
  fi
  python3 - "${python_args[@]}" <<'PY'
import os
import pathlib
import re
import shutil
import stat
import sys
import time

(
    release_root_text,
    active_pointer_text,
    journal_text,
    candidate_root_text,
    deploy_dir_text,
    keep_text,
    grace_text,
    protected_candidate,
    consumed_candidate,
    protected_forward_staging_text,
    explicit_count_text,
    *generation_paths,
) = sys.argv[1:]

release_root = pathlib.Path(release_root_text)
active_pointer = pathlib.Path(active_pointer_text)
journal = pathlib.Path(journal_text)
candidate_root = pathlib.Path(candidate_root_text)
deploy_dir = pathlib.Path(deploy_dir_text)
generation_name = re.compile(r"(?:release-|data-)[A-Za-z0-9._-]+|(?:murmur|legacy)\.[A-Za-z0-9._-]+")
candidate_name = re.compile(r"[0-9a-f]{40}-[1-9][0-9]*-[1-9][0-9]*")
forward_staging_name = re.compile(r"\.crawler-forward-data-[0-9a-f]{40}\.[A-Za-z0-9]+")

try:
    keep = int(keep_text)
    grace = int(grace_text)
    explicit_count = int(explicit_count_text)
except ValueError as error:
    raise SystemExit("invalid publication retention configuration") from error
if not 1 <= keep <= 20 or not 0 <= grace <= 604800:
    raise SystemExit("publication retention configuration is outside safe bounds")
if not 0 <= explicit_count <= len(generation_paths):
    raise SystemExit("invalid publication retention path counts")
explicit_releases = generation_paths[:explicit_count]
verified_release_paths = generation_paths[explicit_count:]


def require_directory(path: pathlib.Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise SystemExit(f"{label} is unavailable") from error
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise SystemExit(f"{label} is unsafe")


def validate_generation(path_text: str, label: str) -> pathlib.Path:
    path = pathlib.Path(path_text)
    if path.parent != release_root or not generation_name.fullmatch(path.name):
        raise SystemExit(f"{label} is outside the release-generation root")
    require_directory(path, label)
    return path


def read_exact_values(path: pathlib.Path) -> dict[str, str]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise SystemExit("publication journal is unsafe")
    if stat.S_IMODE(mode) != 0o600:
        raise SystemExit("publication journal permissions are unsafe")
    values: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            raise SystemExit("publication journal is malformed")
        values.setdefault(key, []).append(value)
    if any(len(items) != 1 for items in values.values()):
        raise SystemExit("publication journal contains duplicate keys")
    return {key: items[0] for key, items in values.items()}


def fsync_directory(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


require_directory(release_root, "release-generation root")
require_directory(deploy_dir, "deploy root")
try:
    active_mode = active_pointer.lstat().st_mode
except FileNotFoundError as error:
    raise SystemExit("active release pointer is unavailable") from error
if not stat.S_ISLNK(active_mode):
    raise SystemExit("active release pointer is unsafe")
active = validate_generation(os.readlink(active_pointer), "active release")

protected_releases = {active}
journal_values = read_exact_values(journal)
if journal_values:
    required = {
        "PUBLICATION_FORMAT_VERSION",
        "SYNC_SUCCEEDED",
        "RECOVERY_ACTION",
        "PREVIOUS_RELEASE",
        "TARGET_RELEASE",
    }
    if set(journal_values) != required or journal_values["PUBLICATION_FORMAT_VERSION"] != "1":
        raise SystemExit("publication journal has an unsupported contract")
    if journal_values["SYNC_SUCCEEDED"] not in {"0", "1"}:
        raise SystemExit("publication journal has an invalid sync state")
    if journal_values["RECOVERY_ACTION"] not in {"restore-previous", "promote-target"}:
        raise SystemExit("publication journal has an invalid recovery action")
    protected_releases.add(
        validate_generation(journal_values["PREVIOUS_RELEASE"], "journal previous release")
    )
    protected_releases.add(
        validate_generation(journal_values["TARGET_RELEASE"], "journal target release")
    )
for release in explicit_releases:
    if release:
        protected_releases.add(validate_generation(release, "explicitly protected release"))
verified_releases = {
    validate_generation(release, "verified v3 release")
    for release in verified_release_paths
}

if protected_candidate and not candidate_name.fullmatch(protected_candidate):
    raise SystemExit("protected CSV candidate identity is invalid")
if consumed_candidate and not candidate_name.fullmatch(consumed_candidate):
    raise SystemExit("consumed CSV candidate identity is invalid")
if candidate_root.exists():
    require_directory(candidate_root, "CSV candidate root")
elif protected_candidate or consumed_candidate:
    raise SystemExit("CSV candidate root is unavailable")
protected_forward_staging = None
if protected_forward_staging_text:
    protected_forward_staging = pathlib.Path(protected_forward_staging_text)
    if (
        protected_forward_staging.parent != deploy_dir
        or not forward_staging_name.fullmatch(protected_forward_staging.name)
    ):
        raise SystemExit("protected forward-data staging path is unsafe")
    require_directory(protected_forward_staging, "protected forward-data staging")

now = time.time()
deleted_generation = False
generation_entries: list[tuple[int, pathlib.Path]] = []
for entry in os.scandir(release_root):
    if not generation_name.fullmatch(entry.name) or entry.is_symlink():
        continue
    if not entry.is_dir(follow_symlinks=False):
        continue
    generation_entries.append((entry.stat(follow_symlinks=False).st_mtime_ns, pathlib.Path(entry.path)))
newest = {
    path
    for _, path in sorted(
        (item for item in generation_entries if item[1] in verified_releases),
        key=lambda item: (item[0], item[1].name),
        reverse=True,
    )[:keep]
}
for mtime_ns, path in generation_entries:
    age = now - (mtime_ns / 1_000_000_000)
    if path in protected_releases or path in newest or age < grace:
        continue
    shutil.rmtree(path)
    deleted_generation = True
if deleted_generation:
    fsync_directory(release_root)

if candidate_root.exists():
    deleted_candidate = False
    if consumed_candidate:
        consumed_path = candidate_root / consumed_candidate
        try:
            consumed_mode = consumed_path.lstat().st_mode
        except FileNotFoundError:
            consumed_mode = None
        if consumed_mode is not None:
            if not stat.S_ISDIR(consumed_mode) or stat.S_ISLNK(consumed_mode):
                raise SystemExit("consumed CSV candidate is unsafe")
            shutil.rmtree(consumed_path)
            deleted_candidate = True
    for entry in os.scandir(candidate_root):
        if not candidate_name.fullmatch(entry.name) or entry.is_symlink():
            continue
        if entry.name == protected_candidate or not entry.is_dir(follow_symlinks=False):
            continue
        age = now - entry.stat(follow_symlinks=False).st_mtime
        if age < grace:
            continue
        shutil.rmtree(entry.path)
        deleted_candidate = True
    if deleted_candidate:
        fsync_directory(candidate_root)

deleted_forward_staging = False
for entry in os.scandir(deploy_dir):
    if not forward_staging_name.fullmatch(entry.name) or entry.is_symlink():
        continue
    path = pathlib.Path(entry.path)
    if path == protected_forward_staging or not entry.is_dir(follow_symlinks=False):
        continue
    age = now - entry.stat(follow_symlinks=False).st_mtime
    if age < grace:
        continue
    shutil.rmtree(path)
    deleted_forward_staging = True
if deleted_forward_staging:
    fsync_directory(deploy_dir)
PY
}

activate_release_generation() {
  local generation="$1"
  python3 - "$generation" "$ACTIVE_RELEASE_POINTER" "$DEPLOY_DIR" <<'PY'
import os
import secrets
import sys

generation, active, parent = sys.argv[1:]
candidate = f"{active}.{secrets.token_hex(16)}"
os.symlink(generation, candidate)
os.replace(candidate, active)
fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

fsync_generation() {
  local generation="$1"
  python3 - "$generation" <<'PY'
import os
import pathlib
import sys

generation = pathlib.Path(sys.argv[1])
for directory, dirnames, filenames in os.walk(generation, topdown=False):
    for name in filenames:
        path = pathlib.Path(directory) / name
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
fd = os.open(generation.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

build_runtime_env() {
  local release="$1" key
  local -a required_env=(
    LOCAL_DATABASE_URL WEB_DATABASE_URL TYPESENSE_HOST TYPESENSE_PORT
    TYPESENSE_PROTOCOL TYPESENSE_OPERATIONS_KEY
  ) matches=()
  RUNTIME_ENV="$(mktemp /run/lock/jobseek-csv-sync-env.XXXXXX)"
  chmod 0600 "$RUNTIME_ENV"
  for key in "${required_env[@]}"; do
    mapfile -t matches < <(grep -E "^${key}=" "$release/environment.env" || true)
    if (( ${#matches[@]} != 1 )) || [[ -z "${matches[0]#*=}" ]]; then
      echo "ERROR: committed CSV sync variable ${key} is missing or duplicated" >&2
      return 1
    fi
    printf '%s\n' "${matches[0]}" >>"$RUNTIME_ENV"
  done
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
  docker run --rm --name "$NAME" --env-file "$RUNTIME_ENV" --network host \
    "${data_args[@]}" -e "CRAWLER_DB_ROLE=$role" "$image" \
    uv run --no-sync crawler sync
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
  python3 - "$temporary" "$PUBLICATION_JOURNAL" "$DEPLOY_DIR" <<'PY'
import os
import sys

temporary, journal, parent = sys.argv[1:]
fd = os.open(temporary, os.O_RDONLY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
os.replace(temporary, journal)
fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

clear_journal() {
  rm -f -- "$PUBLICATION_JOURNAL"
  python3 - "$DEPLOY_DIR" <<'PY'
import os
import sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
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
  python3 - "$archive" "$data_contract" <<'PY'
import hashlib
import pathlib
import re
import tarfile
import sys

archive = pathlib.Path(sys.argv[1])
expected_contract = sys.argv[2]
rows = []
provided_manifest = None
seen_files = set()
with tarfile.open(archive, "r:") as bundle:
    members = bundle.getmembers()
    if not members:
        raise SystemExit("empty CSV candidate archive")
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("unsafe CSV candidate archive path")
        if member.isdir():
            if path != pathlib.PurePosixPath("data") and (
                not path.parts or path.parts[0] != "data"
            ):
                raise SystemExit("unexpected CSV candidate directory")
            continue
        if not member.isfile() or member.name in seen_files:
            raise SystemExit("unsafe or duplicate CSV candidate archive entry")
        seen_files.add(member.name)
        extracted = bundle.extractfile(member)
        if extracted is None:
            raise SystemExit("CSV candidate archive entry is unreadable")
        content = extracted.read()
        if path == pathlib.PurePosixPath("data-files.sha256"):
            provided_manifest = content
            continue
        if not path.parts or path.parts[0] != "data":
            raise SystemExit("unexpected CSV candidate file")
        relative = pathlib.PurePosixPath(*path.parts[1:]).as_posix()
        if not re.fullmatch(r"[A-Za-z0-9._/-]+\.csv", relative):
            raise SystemExit("unexpected CSV candidate file")
        rows.append((relative, hashlib.sha256(content).hexdigest()))
if not rows or provided_manifest is None:
    raise SystemExit("CSV candidate archive is incomplete")
actual_manifest = "".join(
    f"{digest}  {relative}\n" for relative, digest in sorted(rows)
).encode()
if provided_manifest != actual_manifest:
    raise SystemExit("CSV candidate manifest does not match its exact tree")
actual_contract = hashlib.sha256(actual_manifest).hexdigest()
if actual_contract != expected_contract:
    raise SystemExit("CSV candidate data contract does not match its exact tree")
PY
}

prepare_candidate_generation() {
  local revision="$1" data_contract="$2" candidate_id="$3" archive_sha="$4"
  local candidate archive generation compose_digest env_digest success_digest files_digest
  verify_candidate_archive_contract \
    "$revision" "$data_contract" "$candidate_id" "$archive_sha" || return 1
  candidate="$CANDIDATE_ROOT/$candidate_id"
  archive="$candidate/csv-snapshot.tar"
  generation="$(mktemp -d "${ACTIVE_RELEASE_ROOT}/data-${revision}.XXXXXX")"
  install -m 0644 "$ACTIVE_RELEASE/docker-compose.yml" "$generation/docker-compose.yml"
  install -m 0600 "$ACTIVE_RELEASE/environment.env" "$generation/environment.env"
  install -m 0644 "$ACTIVE_RELEASE/success.env" "$generation/success.env"
  if [[ -f "$ACTIVE_RELEASE/rollback-images.override.yml" ]]; then
    install -m 0644 "$ACTIVE_RELEASE/rollback-images.override.yml" \
      "$generation/rollback-images.override.yml"
  fi
  python3 - "$archive" "$generation" <<'PY'
import pathlib
import tarfile
import sys

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
with tarfile.open(archive, "r:") as bundle:
    members = bundle.getmembers()
    if not members:
        raise SystemExit("empty CSV candidate archive")
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("unsafe CSV candidate archive path")
        if member.isdir():
            if path != pathlib.PurePosixPath("data") and path.parts[0] != "data":
                raise SystemExit("unexpected CSV candidate directory")
        elif member.isfile():
            if path == pathlib.PurePosixPath("data-files.sha256"):
                continue
            if path.parts[0] != "data" or path.suffix != ".csv":
                raise SystemExit("unexpected CSV candidate file")
        else:
            raise SystemExit("unsafe CSV candidate archive entry")
    bundle.extractall(destination, filter="data")
PY
  [[ "$(sha256sum "$archive" | awk '{print $1}')" == "$archive_sha" ]] || {
    echo "ERROR: CSV candidate archive changed during preparation" >&2
    return 1
  }
  verify_exact_csv_tree "$generation/data" "$generation/data-files.sha256" || return 1
  compose_digest="$(sha256sum "$generation/docker-compose.yml" | awk '{print $1}')"
  env_digest="$(sha256sum "$generation/environment.env" | awk '{print $1}')"
  success_digest="$(sha256sum "$generation/success.env" | awk '{print $1}')"
  files_digest="$(sha256sum "$generation/data-files.sha256" | awk '{print $1}')"
  [[ "$files_digest" == "$data_contract" ]] || {
    echo "ERROR: CSV candidate data contract changed during preparation" >&2
    return 1
  }
  printf '%s\n' "$compose_digest" >"$generation/docker-compose.sha256"
  printf '%s\n' "$env_digest" >"$generation/environment.sha256"
  printf '%s\n' \
    'RELEASE_FORMAT_VERSION=3' \
    "COMPOSE_SHA256=$compose_digest" \
    "ENVIRONMENT_SHA256=$env_digest" \
    "SUCCESS_SHA256=$success_digest" \
    "DATA_FILES_SHA256=$files_digest" \
    "DATA_CONTRACT_SHA256=$data_contract" \
    "DATA_REVISION=$revision" \
    >"$generation/release.manifest"
  if [[ -f "$generation/rollback-images.override.yml" ]]; then
    printf '%s\n' \
      'HAS_IMAGE_OVERRIDE=1' \
      "IMAGE_OVERRIDE_SHA256=$(sha256sum "$generation/rollback-images.override.yml" | awk '{print $1}')" \
      'BOOTSTRAP_LEGACY=0' >>"$generation/release.manifest"
  else
    printf '%s\n' 'HAS_IMAGE_OVERRIDE=0' >>"$generation/release.manifest"
  fi
  chmod 0644 "$generation"/*.sha256 "$generation/release.manifest"
  fsync_generation "$generation" || return 1
  verify_release_generation "$generation" || return 1
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
  python3 - "$ACTIVE_RELEASE_ROOT" <<'PY'
import os
import sys

fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
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
  DATA_CONTRACT_SHA256="${3:?bootstrap data contract is required}"
  CANDIDATE_ID="${4:?bootstrap candidate ID is required}"
  ARCHIVE_SHA256="${5:?bootstrap archive digest is required}"
  [[ "$REVISION" =~ ^[a-f0-9]{40}$ && "$DATA_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ && \
    "$ARCHIVE_SHA256" =~ ^[a-f0-9]{64}$ && \
    "$CANDIDATE_ID" =~ ^${REVISION}-[1-9][0-9]*-[1-9][0-9]*$ ]] || exit 1
  recover_publication
  verify_candidate_archive_contract \
    "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256"
  load_committed_release
  if [[ "$ACTIVE_RELEASE_FORMAT" == 3 ]]; then
    # A verified v3 generation is the exact live rollback source of truth. A
    # later main revision may contain different CSVs; replacing this evidence
    # before the new deploy commits would make rollback non-atomic.
    prune_publications "" "$CANDIDATE_ID" "" "$ACTIVE_RELEASE"
    exit 0
  fi
  previous_release="$ACTIVE_RELEASE"
  candidate_generation="$(
    prepare_candidate_generation \
      "$REVISION" "$DATA_CONTRACT_SHA256" "$CANDIDATE_ID" "$ARCHIVE_SHA256"
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

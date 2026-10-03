#!/usr/bin/env bash
# Deploy the Hetzner-local Codex runner host surface.
#
# This is deployment-only: it updates the checked-out repo and systemd units.
# It does not start any Codex operational service directly. The separately
# authorized observation-only operation starts one deterministic dispatcher.

set -euo pipefail

ROOT_DIR="${JOBSEEK_CODEX_ROOT:-/srv/jobseek-codex}"
REPO_DIR="${JOBSEEK_CODEX_REPO_DIR:-${ROOT_DIR}/repo}"
REPO_URL="${JOBSEEK_CODEX_REPO_URL:-https://github.com/colophon-group/jobseek.git}"
BRANCH="${JOBSEEK_CODEX_BRANCH:-main}"
EXPECTED_SHA="${JOBSEEK_CODEX_EXPECTED_SHA:-}"
LOCK_TIMEOUT_S="${JOBSEEK_CODEX_DEPLOY_LOCK_TIMEOUT_S:-15000}"
START_TIMERS="${JOBSEEK_CODEX_START_TIMERS:-0}"
CONFIG_DIR="${JOBSEEK_CODEX_CONFIG_DIR:-/etc/jobseek-codex}"
GOVERNOR_ENV_FILE="${CONFIG_DIR}/governor.env"
LABELLER_ENV_FILE="${CONFIG_DIR}/labeller.env"
CODEX_NPM_PREFIX="/home/codex-runner/.local/share/jobseek-codex-cli"
CODEX_BIN="${CODEX_NPM_PREFIX}/bin/codex"

LOCK_FILE="${ROOT_DIR}/state/codex-runner.lock"
# CI copies this complete bundle into a root-owned deployment directory.
TRUSTED_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRIVILEGED_DIR=/usr/local/lib/jobseek-codex
OBSERVATION_TIMER=jobseek-codex-company-selection-observation.timer

UNITS=(
  jobseek-codex-docker-lifecycle.service
  jobseek-codex-governor.service
  jobseek-codex-governor.timer
  jobseek-codex-daily-annotations.service
  jobseek-codex-daily-annotations.timer
  jobseek-codex-daily-error-review.service
  jobseek-codex-daily-error-review.timer
  jobseek-codex-company-selection-observation.service
  jobseek-codex-company-selection-observation.timer
)

TIMERS=(
  jobseek-codex-governor.timer
  jobseek-codex-daily-annotations.timer
  jobseek-codex-daily-error-review.timer
  jobseek-codex-company-selection-observation.timer
)

ALWAYS_ON_SERVICES=(
  jobseek-codex-docker-lifecycle.service
)

ACTIVE_TIMERS_BEFORE_DEPLOY=()
ENABLED_TIMERS_BEFORE_DEPLOY=()
TIMER_RESTORE_ARMED=0
LABELLER_CONTRACT_VERIFIED=0

log() {
  printf '==> %s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

as_runner() {
  runuser -u codex-runner -- "$@"
}

governor_number() {
  local key="$1"
  local fallback="$2"
  local line=""
  local value=""
  if line="$(grep -E "^${key}=" "${GOVERNOR_ENV_FILE}" | tail -n 1)" && [[ -n "${line}" ]]; then
    value="${line#*=}"
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
  else
    value="${fallback}"
  fi
  [[ "${value}" =~ ^[0-9]+([.][0-9]+)?$ ]] ||
    fail "${key} must be a non-negative number"
  printf '%s\n' "${value}"
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    fail "must run as root"
  fi
}

_validate_labeller_env_file() {
  local path="$1"
  local line=""
  local trimmed=""
  local value=""
  local first=""
  local last=""
  local line_number=0
  local assignment_count=0

  while IFS= read -r line || [[ -n "${line}" ]]; do
    ((line_number += 1))
    line="${line%$'\r'}"
    trimmed="${line#"${line%%[![:space:]]*}"}"
    if [[ -z "${trimmed}" || "${trimmed:0:1}" == "#" ]]; then
      continue
    fi
    if [[ "${line}" != LOCAL_DATABASE_URL=* ]]; then
      printf 'ERROR: labeller.env line %d is not an allowed LOCAL_DATABASE_URL assignment\n' \
        "${line_number}" >&2
      return 1
    fi

    ((assignment_count += 1))
    if ((assignment_count > 1)); then
      printf 'ERROR: labeller.env contains duplicate LOCAL_DATABASE_URL assignments\n' >&2
      return 1
    fi

    value="${line#LOCAL_DATABASE_URL=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    first="${value:0:1}"
    last="${value: -1}"
    if [[ "${first}" == "\"" || "${first}" == "'" || "${last}" == "\"" || "${last}" == "'" ]]; then
      if [[ ${#value} -lt 2 ]] ||
        ! { [[ "${first}" == "\"" && "${last}" == "\"" ]] ||
          [[ "${first}" == "'" && "${last}" == "'" ]]; }; then
        printf 'ERROR: labeller.env LOCAL_DATABASE_URL has mismatched quotes\n' >&2
        return 1
      fi
      value="${value:1:${#value}-2}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    if [[ -z "${value}" ]]; then
      printf 'ERROR: labeller.env LOCAL_DATABASE_URL must not be empty\n' >&2
      return 1
    fi
    if [[ "${value}" == *[[:space:]]* ]]; then
      printf 'ERROR: labeller.env LOCAL_DATABASE_URL must not contain whitespace\n' >&2
      return 1
    fi
    if [[ "${value}" != postgresql://?* && "${value}" != postgres://?* ]]; then
      printf 'ERROR: labeller.env LOCAL_DATABASE_URL must be a PostgreSQL DSN\n' >&2
      return 1
    fi
  done <"${path}"

  if ((assignment_count != 1)); then
    printf 'ERROR: labeller.env must contain exactly one LOCAL_DATABASE_URL assignment\n' >&2
    return 1
  fi
}

ensure_layout() {
  if ! id -u codex-runner >/dev/null 2>&1; then
    useradd --system --user-group --create-home \
      --home-dir /home/codex-runner --shell /bin/bash codex-runner
  fi

  if getent group docker >/dev/null 2>&1 && id -nG codex-runner | tr ' ' '\n' | grep -qx docker; then
    gpasswd --delete codex-runner docker
  fi

  [[ ! -L "${ROOT_DIR}" ]] || fail "runner root must not be a symlink"
  install -d -o root -g codex-runner -m 0750 "${ROOT_DIR}"
  [[ ! -L "${ROOT_DIR}/inputs" ]] || fail "inputs must not be a symlink"
  # ROOT_DIR now prevents replacement of these entries. Do not chmod/chown
  # paths inside runner-writable directories as root.
  local directory
  for directory in worktrees traces state logs repo data; do
    [[ ! -L "${ROOT_DIR}/${directory}" ]] || fail "runner directory is a symlink: ${directory}"
    if [[ ! -d "${ROOT_DIR}/${directory}" ]]; then
      install -d -o codex-runner -g codex-runner -m 0700 "${ROOT_DIR}/${directory}"
    fi
  done
  as_runner mkdir -p "${ROOT_DIR}/data/postings-labelled"
  install -d -o root -g codex-runner -m 0750 "${ROOT_DIR}/inputs" /etc/jobseek-codex
  # Previously the parent was agent-owned. Reject any replaced descendants.
  python3 -I - "${ROOT_DIR}/inputs" <<'PYTHON'
import stat, sys
from pathlib import Path
root = Path(sys.argv[1])
for path in [root, *root.rglob("*")]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        # The collector's relative latest link is data, not executable code.
        if path.name == "latest" and path.parent == root / "error-review":
            continue
        raise SystemExit(f"unsafe input symlink: {path}")
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit(f"unsafe privileged input path: {path}")
PYTHON

  as_runner touch "${LOCK_FILE}"
  as_runner mkdir -p -m 0700 "${ROOT_DIR}/state/company-selection-observation"
}

ensure_document_extraction_runtime() {
  if command -v tesseract >/dev/null 2>&1 &&
    tesseract --list-langs 2>/dev/null | grep -qx eng; then
    return
  fi

  log "installing Codex runner PDF OCR runtime"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng
  rm -rf /var/lib/apt/lists/*

  command -v tesseract >/dev/null 2>&1 || fail "tesseract executable is unavailable"
  tesseract --list-langs 2>/dev/null | grep -qx eng ||
    fail "Tesseract English language data is unavailable"
}

ensure_codex_cli() {
  command -v npm >/dev/null 2>&1 || fail "npm is required to install the Codex CLI"
  local version
  version="$(cat "${TRUSTED_SOURCE}/deploy/codex-version")"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid Codex version pin"
  CODEX_NPM_PREFIX="/home/codex-runner/.local/share/jobseek-codex-cli/${version}"
  CODEX_BIN="${CODEX_NPM_PREFIX}/bin/codex"
  as_runner mkdir -p /home/codex-runner/.local/bin "${CODEX_NPM_PREFIX}"
  log "installing Codex CLI ${version}"
  as_runner timeout 180s npm install --global --prefix "${CODEX_NPM_PREFIX}" "@openai/codex@${version}"
  as_runner "${CODEX_BIN}" --version

  # Verify the server accepts the model before a resolver run can claim an
  # issue. Keep this probe outside the repository and leave no session files.
  local smoke_output=""
  if ! smoke_output="$(as_runner timeout 120s "${CODEX_BIN}" exec \
      --json --ephemeral --ignore-user-config --ignore-rules \
      --skip-git-repo-check -C /tmp \
      -m gpt-6.1-sol -c model_reasoning_effort=low \
      'Reply exactly OK.' </dev/null 2>&1)"; then
    fail "Codex CLI model smoke failed; check runner authentication and model compatibility"
  fi
  if ! python3 -c \
    'import json, sys; lines = (json.loads(line) for line in sys.stdin if line.startswith("{")); raise SystemExit(0 if any(line.get("type") == "item.completed" and line.get("item", {}).get("type") == "agent_message" and line["item"].get("text", "").strip() == "OK" for line in lines) else 1)' \
    <<<"${smoke_output}"; then
    fail "Codex CLI model smoke did not return exactly OK"
  fi

  as_runner "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/codex-agent-smoke.py" --codex "${CODEX_BIN}"

  as_runner ln -sfnT "${CODEX_BIN}" /home/codex-runner/.local/bin/codex
  as_runner env PATH="/home/codex-runner/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    codex --version
}

require_runtime_config() {
  [[ -r "${GOVERNOR_ENV_FILE}" ]] || fail "missing ${GOVERNOR_ENV_FILE}"
  [[ -r "${LABELLER_ENV_FILE}" ]] || fail "missing ${LABELLER_ENV_FILE}"
  as_runner test -r "${GOVERNOR_ENV_FILE}" ||
    fail "codex-runner cannot read governor.env"
  as_runner test -r "${LABELLER_ENV_FILE}" ||
    fail "codex-runner cannot read labeller.env"
  _validate_labeller_env_file "${LABELLER_ENV_FILE}" ||
    fail "invalid labeller.env; keep it DSN-only, correct it without printing the secret, and rerun this deployment"
}

update_repo() {
  if [[ -d "${REPO_DIR}/.git" ]]; then
    if ! as_runner git -C "${REPO_DIR}" diff --quiet ||
      ! as_runner git -C "${REPO_DIR}" diff --cached --quiet; then
      fail "${REPO_DIR} has tracked local changes; refusing to overwrite"
    fi
    as_runner git -C "${REPO_DIR}" remote set-url origin "${REPO_URL}"
    as_runner git -C "${REPO_DIR}" fetch --prune origin "${BRANCH}"
  else
    rm -rf "${REPO_DIR}"
    install -d -o codex-runner -g codex-runner -m 0750 "${REPO_DIR}"
    as_runner git clone --branch "${BRANCH}" "${REPO_URL}" "${REPO_DIR}"
    as_runner git -C "${REPO_DIR}" fetch --prune origin "${BRANCH}"
  fi

  local checkout_ref="origin/${BRANCH}"
  if [[ -n "${EXPECTED_SHA}" ]]; then
    if ! as_runner git -C "${REPO_DIR}" cat-file -e "${EXPECTED_SHA}^{commit}" 2>/dev/null; then
      as_runner git -C "${REPO_DIR}" fetch origin "${EXPECTED_SHA}"
    fi
    checkout_ref="${EXPECTED_SHA}"
  fi

  # This clone is also the Git common directory for resolver worktrees. Keep
  # its deployment checkout detached so an unrelated local branch-ref update
  # cannot make an unchanged index/worktree appear dirty on the next deploy.
  # Resolver worktrees continue to start from the freshly fetched
  # ``origin/${BRANCH}`` ref.
  as_runner git -C "${REPO_DIR}" checkout --detach "${checkout_ref}"
  if as_runner git -C "${REPO_DIR}" symbolic-ref -q HEAD >/dev/null; then
    fail "${REPO_DIR} deployment checkout must have detached HEAD"
  fi
  local actual_sha
  actual_sha="$(as_runner git -C "${REPO_DIR}" rev-parse HEAD)"
  log "repo ${REPO_DIR} at ${actual_sha}"

  if [[ -n "${EXPECTED_SHA}" && "${actual_sha}" != "${EXPECTED_SHA}" ]]; then
    fail "expected ${EXPECTED_SHA}, deployed ${actual_sha}"
  fi
}

sync_crawler_runtime() {
  as_runner env \
    PATH="/home/codex-runner/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    bash -c "cd '${REPO_DIR}/apps/crawler' && uv sync --frozen --no-dev"
}

verify_trusted_bundle() {
  # Check every ancestor too: a root-owned file under an agent-writable
  # parent can still be replaced. Never run/import code from REPO_DIR as root.
  python3 -I - "${TRUSTED_SOURCE}" <<'PYTHON'
import os, stat, sys
from pathlib import Path
root = Path(sys.argv[1])
paths = [root, *root.parents, *root.rglob("*")]
for path in paths:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit(f"unsafe trusted deployment path: {path} (uid={info.st_uid}, mode={stat.S_IMODE(info.st_mode):o})")
PYTHON
}

install_privileged_runtime() {
  [[ ! -L "${PRIVILEGED_DIR}" ]] || fail "privileged runtime must not be a symlink"
  install -d -o root -g root -m 0755 "${PRIVILEGED_DIR}"
  local script
  for script in codex-routine-status.py codex-error-review-bundle.py \
      codex-docker-lifecycle-watch.py codex-company-selection-observation-dispatch.py \
      jobseek_maintenance_provenance.py; do
    install -o root -g root -m 0644 "${TRUSTED_SOURCE}/scripts/${script}" \
      "${PRIVILEGED_DIR}/${script}"
  done
  # Record both the deployment and pinned CLI for incident/rollback evidence.
  printf 'revision=%s\ncodex_version=%s\n' "${EXPECTED_SHA}" \
    "$(cat "${TRUSTED_SOURCE}/deploy/codex-version")" >"${PRIVILEGED_DIR}/release.txt"
}

install_maintenance_contract() {
  install -d -o root -g root -m 0755 /usr/local/lib/jobseek-maintenance
  install -o root -g root -m 0644 \
    "${TRUSTED_SOURCE}/scripts/jobseek_maintenance_provenance.py" \
    /usr/local/lib/jobseek-maintenance/jobseek_maintenance_provenance.py
  install -o root -g root -m 0755 \
    "${TRUSTED_SOURCE}/scripts/jobseek-maintenance.py" \
    /usr/local/sbin/jobseek-maintenance
}

install_units() {
  local unit
  for unit in "${UNITS[@]}"; do
    install -o root -g root -m 0644 \
      "${TRUSTED_SOURCE}/deploy/systemd/${unit}" \
      "/etc/systemd/system/${unit}"
  done

  systemctl daemon-reload
  systemd-analyze verify "${UNITS[@]/#//etc/systemd/system/}"
  restore_timer_enablement
  systemctl enable "${ALWAYS_ON_SERVICES[@]}"
}

timer_in_list() {
  local expected="$1"
  shift
  local timer
  for timer in "$@"; do
    if [[ "${timer}" == "${expected}" ]]; then
      return 0
    fi
  done
  return 1
}

restore_timer_enablement() {
  local timer
  local -a enable_candidates=()
  local -a disable_candidates=()

  for timer in "${TIMERS[@]}"; do
    if timer_in_list "${timer}" "${ENABLED_TIMERS_BEFORE_DEPLOY[@]-}" ||
      { [[ "${START_TIMERS}" == "1" && "${timer}" != "${OBSERVATION_TIMER}" ]]; }; then
      enable_candidates+=("${timer}")
    else
      disable_candidates+=("${timer}")
    fi
  done

  if ((${#enable_candidates[@]} > 0)); then
    systemctl enable "${enable_candidates[@]}"
  fi
  if ((${#disable_candidates[@]} > 0)); then
    systemctl disable "${disable_candidates[@]}"
  fi
}

start_always_on_services() {
  # The watcher imports its implementation at process start, so a repo update
  # must restart it even when the unit definition itself did not change.
  systemctl restart "${ALWAYS_ON_SERVICES[@]}"
  local service
  for service in "${ALWAYS_ON_SERVICES[@]}"; do
    systemctl is-active --quiet "${service}" || fail "${service} failed to start"
  done
}

verify_entrypoints() {
  as_runner env -i HOME=/home/codex-runner PATH=/usr/local/bin:/usr/bin:/bin \
    python3 -I "${PRIVILEGED_DIR}/codex-company-selection-observation-dispatch.py" --help >/dev/null
  as_runner python3 -m py_compile \
    "${REPO_DIR}/scripts/codex-company-resolver-governor.py" \
    "${REPO_DIR}/scripts/codex-daily-routine-runner.py" \
    "${REPO_DIR}/scripts/codex-docker-lifecycle-watch.py" \
    "${REPO_DIR}/scripts/codex-error-review-bundle.py" \
    "${REPO_DIR}/scripts/codex-routine-status.py" \
    "${REPO_DIR}/scripts/jobseek_maintenance_provenance.py" \
    "${REPO_DIR}/scripts/jobseek-maintenance.py" \
    "${REPO_DIR}/scripts/codex-trace-backfill.py" \
    "${REPO_DIR}/scripts/codex-worktree-reconcile.py" \
    "${REPO_DIR}/scripts/codex-usage-probe.py" \
    "${REPO_DIR}/apps/crawler/src/workspace/codex_runner.py" \
    "${REPO_DIR}/apps/crawler/src/workspace/codex_routine_runner.py" \
    "${REPO_DIR}/apps/crawler/src/workspace/trace_backfill.py" \
    "${REPO_DIR}/apps/crawler/src/workspace/worktree_reconcile.py"
  as_runner env PYTHONPATH="${REPO_DIR}/apps/crawler" \
    "${REPO_DIR}/apps/crawler/.venv/bin/python" -c \
    'import src.workspace.codex_runner; import src.workspace.codex_routine_runner; import src.workspace.trace_backfill'
  as_runner env PYTHONPATH="${REPO_DIR}/apps/crawler" \
    "${REPO_DIR}/apps/crawler/.venv/bin/python" -c \
    'import sys; from pathlib import Path; from src.workspace.codex_routine_runner import labeller_postgresql_child_env; state=Path(sys.argv[1]); actual=labeller_postgresql_child_env(state); expected={"CRAWLER_DB_ROLE":"labeller","CRAWLER_DB_POOL_MIN":"0","CRAWLER_DB_POOL_MAX":"2","CRAWLER_DB_POOL_IDLE_SECONDS":"60","JOBSEEK_LABELLER_DB_LOCK_FILE":str(state / "labeller-postgresql.lock"),"JOBSEEK_LABELLER_DB_LOCK_TIMEOUT_SECONDS":"300"}; raise SystemExit(0 if actual == expected else "labeller PostgreSQL pool contract mismatch; keep labeller.env DSN-only, restore the committed runner contract, and redeploy")' \
    "${ROOT_DIR}/state"
  as_runner env PYTHONPATH="${REPO_DIR}/apps/crawler" \
    "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/check-agent-contracts.py" >"${PRIVILEGED_DIR}/agent-contracts.json"
  LABELLER_CONTRACT_VERIFIED=1
  as_runner "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/codex-trace-backfill.py" --help >/dev/null
  python3 -I "${PRIVILEGED_DIR}/codex-error-review-bundle.py" --help >/dev/null
  python3 /usr/local/sbin/jobseek-maintenance --self-test >/dev/null
  as_runner "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/codex-worktree-reconcile.py" --help >/dev/null
}

reconcile_codex_worktrees() {
  log "Codex worktree reconciliation"
  as_runner env PYTHONPATH="${REPO_DIR}/apps/crawler" \
    "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/codex-worktree-reconcile.py" --apply --summary-only
}

report_trace_retention() {
  log "Codex trace retention report"
  as_runner env PYTHONPATH="${REPO_DIR}/apps/crawler" \
    "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/codex-trace-backfill.py" --report \
    --min-disk-free-gib "$(governor_number JOBSEEK_CODEX_MIN_DISK_FREE_GIB 5)" \
    --disk-alert-margin-gib "$(governor_number JOBSEEK_CODEX_DISK_ALERT_MARGIN_GIB 2)" \
    --max-quarantine-runs "$(governor_number JOBSEEK_CODEX_MAX_QUARANTINE_RUNS 50)" \
    --max-quarantine-gib "$(governor_number JOBSEEK_CODEX_MAX_QUARANTINE_GIB 2)" \
    --max-retained-session-files \
      "$(governor_number JOBSEEK_CODEX_MAX_RETAINED_SESSION_FILES 500)" \
    --max-retained-session-gib \
      "$(governor_number JOBSEEK_CODEX_MAX_RETAINED_SESSION_GIB 2)" \
    --max-unlinked-session-age-days \
      "$(governor_number JOBSEEK_CODEX_MAX_UNLINKED_SESSION_AGE_DAYS 7)"
}

pause_timer_activations() {
  local timer
  for timer in "${TIMERS[@]}"; do
    if systemctl is-enabled --quiet "${timer}"; then
      ENABLED_TIMERS_BEFORE_DEPLOY+=("${timer}")
    fi
    if systemctl is-active --quiet "${timer}"; then
      ACTIVE_TIMERS_BEFORE_DEPLOY+=("${timer}")
    fi
  done

  TIMER_RESTORE_ARMED=1
  trap restore_timers_on_exit EXIT

  if ((${#ACTIVE_TIMERS_BEFORE_DEPLOY[@]} > 0)); then
    log "pausing new Codex timer activations while deployment waits"
    systemctl stop "${ACTIVE_TIMERS_BEFORE_DEPLOY[@]}"
  fi
}

restore_timers_on_exit() {
  local deploy_status=$?
  local restore_status=0
  local timer
  local -a restore_candidates=()
  local -a safe_restore=()
  trap - EXIT
  set +e

  if [[ "${TIMER_RESTORE_ARMED}" == "1" ]]; then
    if [[ "${START_TIMERS}" == "1" ]]; then
      for timer in "${TIMERS[@]}"; do
        if [[ "${timer}" != "${OBSERVATION_TIMER}" ]] ||
          timer_in_list "${timer}" "${ACTIVE_TIMERS_BEFORE_DEPLOY[@]-}"; then
          restore_candidates+=("${timer}")
        fi
      done
    elif ((${#ACTIVE_TIMERS_BEFORE_DEPLOY[@]} > 0)); then
      restore_candidates=("${ACTIVE_TIMERS_BEFORE_DEPLOY[@]}")
    fi
    for timer in "${restore_candidates[@]-}"; do
      [[ -n "${timer}" ]] || continue
      if [[ "${timer}" == "jobseek-codex-daily-annotations.timer" ]] &&
        [[ "${LABELLER_CONTRACT_VERIFIED}" != "1" ]]; then
        log "leaving ${timer} stopped: labeller PostgreSQL contract was not verified"
      else
        safe_restore+=("${timer}")
      fi
    done
    if ((${#safe_restore[@]} > 0)); then
      systemctl start "${safe_restore[@]}"
      restore_status=$?
    fi
  fi

  systemctl list-timers --all 'jobseek-codex*' --no-pager
  if [[ "${deploy_status}" -eq 0 && "${restore_status}" -ne 0 ]]; then
    deploy_status="${restore_status}"
  fi
  exit "${deploy_status}"
}

main() {
  require_root
  verify_trusted_bundle
  ensure_layout
  pause_timer_activations
  require_runtime_config

  log "waiting for Codex runner lock: ${LOCK_FILE}"
  exec 9<"${LOCK_FILE}"
  if ! flock -w "${LOCK_TIMEOUT_S}" 9; then
    fail "could not acquire ${LOCK_FILE} within ${LOCK_TIMEOUT_S}s"
  fi

  ensure_document_extraction_runtime
  update_repo
  sync_crawler_runtime
  ensure_codex_cli
  install_privileged_runtime
  install_maintenance_contract
  install_units
  verify_entrypoints
  start_always_on_services
  reconcile_codex_worktrees
  report_trace_retention
}

activate_company_selection_observation_timer() {
  [[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ && "${BRANCH}" == main &&
    "${START_TIMERS}" == 0 &&
    "${JOBSEEK_CODEX_OBSERVATION_CONFIRMATION:-}" == ACTIVATE-COMPANY-SELECTION-OBSERVATION ]] ||
    fail "targeted observation activation identity is invalid"
  [[ "$(sed -n 's/^revision=//p' "${PRIVILEGED_DIR}/release.txt")" == "${EXPECTED_SHA}" ]] ||
    fail "deploy the exact reviewed runner revision before activation"
  local path bundle_path
  for path in "${PRIVILEGED_DIR}/codex-company-selection-observation-dispatch.py" \
    "/etc/systemd/system/jobseek-codex-company-selection-observation.service" \
    "/etc/systemd/system/${OBSERVATION_TIMER}"; do
    [[ -f "$path" && ! -L "$path" && "$(stat -c '%u:%a' "$path")" == 0:644 ]] ||
      fail "observation runtime or unit is not root-owned and read-only"
    if [[ "$path" == "${PRIVILEGED_DIR}/codex-company-selection-observation-dispatch.py" ]]; then
      bundle_path="${TRUSTED_SOURCE}/scripts/${path##*/}"
    else
      bundle_path="${TRUSTED_SOURCE}/deploy/systemd/${path##*/}"
    fi
    cmp -s "$path" "$bundle_path" || fail "installed observation bytes differ from the trusted deployment bundle"
  done
  ! id -nG codex-runner | tr ' ' '\n' | grep -qx docker || fail "dispatcher must have no Docker membership"
  # Only GH identity, current-main metadata and validated checkpoint artifacts
  # are read. No environment files or provider/DB credentials enter this helper.
  as_runner env -i HOME=/home/codex-runner PATH=/usr/local/bin:/usr/bin:/bin \
    python3 -I "${PRIVILEGED_DIR}/codex-company-selection-observation-dispatch.py" \
    --check --expected-source "${EXPECTED_SHA}" || fail "observation identity or current coverage check failed"
  systemctl start jobseek-codex-company-selection-observation.service
  systemctl enable --now "${OBSERVATION_TIMER}"
  systemctl is-active --quiet "${OBSERVATION_TIMER}" || fail "observation timer did not start"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ $# == 1 && "$1" == --activate-company-selection-observation ]]; then
    require_root
    verify_trusted_bundle
    activate_company_selection_observation_timer
  elif [[ $# == 0 ]]; then
    main
  else
    fail "unknown deployment operation"
  fi
fi

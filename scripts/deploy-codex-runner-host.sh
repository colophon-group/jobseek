#!/usr/bin/env bash
# Deploy the Hetzner-local Codex runner host surface.
#
# This is deployment-only: it updates the checked-out repo and systemd units.
# It does not start any Codex operational service directly.

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

LOCK_FILE="${ROOT_DIR}/state/codex-runner.lock"

UNITS=(
  jobseek-codex-docker-lifecycle.service
  jobseek-codex-governor.service
  jobseek-codex-governor.timer
  jobseek-codex-daily-annotations.service
  jobseek-codex-daily-annotations.timer
  jobseek-codex-daily-error-review.service
  jobseek-codex-daily-error-review.timer
)

TIMERS=(
  jobseek-codex-governor.timer
  jobseek-codex-daily-annotations.timer
  jobseek-codex-daily-error-review.timer
)

ALWAYS_ON_SERVICES=(
  jobseek-codex-docker-lifecycle.service
)

ACTIVE_TIMERS_BEFORE_DEPLOY=()
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

  install -d -o codex-runner -g codex-runner -m 0750 "${ROOT_DIR}"
  install -d -o codex-runner -g codex-runner -m 0700 \
    "${ROOT_DIR}/worktrees" \
    "${ROOT_DIR}/traces" \
    "${ROOT_DIR}/state" \
    "${ROOT_DIR}/logs" \
    "${ROOT_DIR}/data/postings-labelled"
  install -d -o root -g codex-runner -m 0750 "${ROOT_DIR}/inputs" /etc/jobseek-codex

  touch "${LOCK_FILE}"
  chown codex-runner:codex-runner "${LOCK_FILE}"
  chmod 0600 "${LOCK_FILE}"
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
    install -d -o codex-runner -g codex-runner -m 0750 "$(dirname "${REPO_DIR}")"
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

install_maintenance_contract() {
  install -d -o root -g root -m 0755 /usr/local/lib/jobseek-maintenance
  install -o root -g root -m 0644 \
    "${REPO_DIR}/scripts/jobseek_maintenance_provenance.py" \
    /usr/local/lib/jobseek-maintenance/jobseek_maintenance_provenance.py
  install -o root -g root -m 0755 \
    "${REPO_DIR}/scripts/jobseek-maintenance.py" \
    /usr/local/sbin/jobseek-maintenance
}

install_units() {
  local unit
  for unit in "${UNITS[@]}"; do
    install -o root -g root -m 0644 \
      "${REPO_DIR}/deploy/systemd/${unit}" \
      "/etc/systemd/system/${unit}"
  done

  systemctl daemon-reload
  systemd-analyze verify "${UNITS[@]/#//etc/systemd/system/}"
  systemctl enable "${TIMERS[@]}"
  systemctl enable "${ALWAYS_ON_SERVICES[@]}"
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
  LABELLER_CONTRACT_VERIFIED=1
  as_runner "${REPO_DIR}/apps/crawler/.venv/bin/python" \
    "${REPO_DIR}/scripts/codex-trace-backfill.py" --help >/dev/null
  python3 "${REPO_DIR}/scripts/codex-error-review-bundle.py" --help >/dev/null
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
      restore_candidates=("${TIMERS[@]}")
    elif ((${#ACTIVE_TIMERS_BEFORE_DEPLOY[@]} > 0)); then
      restore_candidates=("${ACTIVE_TIMERS_BEFORE_DEPLOY[@]}")
    fi
    for timer in "${restore_candidates[@]}"; do
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
  ensure_layout
  pause_timer_activations
  require_runtime_config

  log "waiting for Codex runner lock: ${LOCK_FILE}"
  exec 9>"${LOCK_FILE}"
  if ! flock -w "${LOCK_TIMEOUT_S}" 9; then
    fail "could not acquire ${LOCK_FILE} within ${LOCK_TIMEOUT_S}s"
  fi

  update_repo
  sync_crawler_runtime
  install_maintenance_contract
  install_units
  verify_entrypoints
  start_always_on_services
  reconcile_codex_worktrees
  report_trace_retention
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi

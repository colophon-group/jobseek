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
LOCK_TIMEOUT_S="${JOBSEEK_CODEX_DEPLOY_LOCK_TIMEOUT_S:-900}"
START_TIMERS="${JOBSEEK_CODEX_START_TIMERS:-0}"

LOCK_FILE="${ROOT_DIR}/state/codex-runner.lock"

UNITS=(
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

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    fail "must run as root"
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
  [[ -r /etc/jobseek-codex/governor.env ]] || fail "missing /etc/jobseek-codex/governor.env"
  [[ -r /etc/jobseek-codex/labeller.env ]] || fail "missing /etc/jobseek-codex/labeller.env"
  as_runner test -r /etc/jobseek-codex/governor.env ||
    fail "codex-runner cannot read governor.env"
  as_runner test -r /etc/jobseek-codex/labeller.env ||
    fail "codex-runner cannot read labeller.env"
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

  as_runner git -C "${REPO_DIR}" checkout -B "${BRANCH}" "${checkout_ref}"
  as_runner git -C "${REPO_DIR}" branch --set-upstream-to="origin/${BRANCH}" "${BRANCH}" >/dev/null
  local actual_sha
  actual_sha="$(as_runner git -C "${REPO_DIR}" rev-parse HEAD)"
  log "repo ${REPO_DIR} at ${actual_sha}"

  if [[ -n "${EXPECTED_SHA}" && "${actual_sha}" != "${EXPECTED_SHA}" ]]; then
    fail "expected ${EXPECTED_SHA}, deployed ${actual_sha}"
  fi
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
}

verify_entrypoints() {
  as_runner python3 -m py_compile \
    "${REPO_DIR}/scripts/codex-company-resolver-governor.py" \
    "${REPO_DIR}/scripts/codex-daily-routine-runner.py" \
    "${REPO_DIR}/scripts/codex-error-review-bundle.py" \
    "${REPO_DIR}/scripts/codex-usage-probe.py" \
    "${REPO_DIR}/apps/crawler/src/workspace/codex_runner.py" \
    "${REPO_DIR}/apps/crawler/src/workspace/codex_routine_runner.py"
  as_runner env PYTHONPATH="${REPO_DIR}/apps/crawler" python3 -c \
    'import src.workspace.codex_runner; import src.workspace.codex_routine_runner'
  python3 "${REPO_DIR}/scripts/codex-error-review-bundle.py" --help >/dev/null
}

maybe_start_timers() {
  if [[ "${START_TIMERS}" == "1" ]]; then
    systemctl start "${TIMERS[@]}"
  fi
}

main() {
  require_root
  ensure_layout
  require_runtime_config

  log "waiting for Codex runner lock: ${LOCK_FILE}"
  exec 9>"${LOCK_FILE}"
  if ! flock -w "${LOCK_TIMEOUT_S}" 9; then
    fail "could not acquire ${LOCK_FILE} within ${LOCK_TIMEOUT_S}s"
  fi

  update_repo
  install_units
  verify_entrypoints
  maybe_start_timers
  systemctl list-timers --all 'jobseek-codex*' --no-pager
}

main "$@"

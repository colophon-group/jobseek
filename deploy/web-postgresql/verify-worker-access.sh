#!/usr/bin/env bash
# Read-only proof of the same restricted login used by deployment and runtime.
set -euo pipefail
: "${PGPASSWORD:?}"
export PGHOST=web-postgres.jseek.co PGPORT=5433 PGUSER=jobseek_narrowed_worker
export PGDATABASE=jobseek_web PGSSLMODE=verify-full PGSSLROOTCERT=system PGCONNECT_TIMEOUT=5
export PGOPTIONS='-c default_transaction_read_only=on'
image=postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193
result=$(docker run --rm --network host --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE -e PGSSLMODE -e PGSSLROOTCERT -e PGCONNECT_TIMEOUT -e PGOPTIONS \
  "$image" psql -X -w -A -t -q -v ON_ERROR_STOP=1 -c "SELECT
    current_user='jobseek_narrowed_worker' AND rolcanlogin AND NOT rolinherit AND rolconnlimit=6
    AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    AND current_setting('timezone')='UTC' AND current_setting('statement_timeout')='45s'
    AND current_setting('idle_in_transaction_session_timeout')='15s'
    AND NOT EXISTS(SELECT FROM pg_auth_members WHERE member=pg_roles.oid)
    FROM pg_roles WHERE rolname=current_user")
test "$result" = t
printf '%s\n' '{"success":true,"deployment_runner_worker_tls_login_verified":true}'

#!/usr/bin/env bash
# Prove pgBackRest can create, back up, and restore through the configured
# private repository mount without touching production data or PostgreSQL.
set -euo pipefail

IMAGE="${JOBSEEK_POSTGRES_BACKUP_IMAGE:-jobseek-postgres:16-pgbackrest}"
SOURCE_CONFIG="${JOBSEEK_POSTGRES_BACKUP_CONFIG:-/etc/jobseek-backup/postgresql}"
REPOSITORY_DIR="${JOBSEEK_POSTGRES_BACKUP_REPOSITORY:-/mnt/jobseek-postgresql-backups}"
BASE_DIR="${JOBSEEK_POSTGRES_BACKUP_SMOKE_DIR:-/var/lib/jobseek-backup/postgresql/compatibility}"
STANZA="compat$(date -u +%Y%m%d%H%M%S)${RANDOM}"
CONTAINER="jobseek-pgbackrest-${STANZA}"
WORK_DIR="${BASE_DIR}/${STANZA}"
CONFIG_DIR="${WORK_DIR}/config"
DATA_DIR="${WORK_DIR}/data"
SPOOL_DIR="${WORK_DIR}/spool"
CONFIG_FILE="${CONFIG_DIR}/pgbackrest.conf"
REPO_CREATED=0

delete_compatibility_stanza() {
  docker run --rm \
    --user 70:70 \
    --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
    --volume "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
    --volume "$DATA_DIR:/var/lib/postgresql/data" \
    --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
    --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
    --entrypoint sh \
    "$IMAGE" \
    -ec 'pgbackrest --stanza="$1" stop; pgbackrest --stanza="$1" --force stanza-delete' \
    _ "$STANZA"
  local repo_path="$REPOSITORY_DIR/compatibility/$STANZA"
  [[ "$repo_path" == "$REPOSITORY_DIR"/compatibility/compat[0-9]* ]]
  rmdir "$repo_path/backup" "$repo_path/archive" "$repo_path"
  rmdir "$REPOSITORY_DIR/compatibility" 2>/dev/null || true
}

cleanup() {
  local rc=$?
  trap - EXIT
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if [[ "$REPO_CREATED" -eq 1 && -s "$CONFIG_FILE" ]]; then
    delete_compatibility_stanza >/dev/null 2>&1 || \
      echo "WARNING: compatibility stanza cleanup failed: $STANZA" >&2
  fi
  rm -rf "$WORK_DIR"
  exit "$rc"
}
trap cleanup EXIT

test -s "$SOURCE_CONFIG/pgbackrest.conf"
mountpoint -q "$REPOSITORY_DIR"
[[ "$(findmnt -n -o FSTYPE "$REPOSITORY_DIR")" == cifs ]]
docker image inspect "$IMAGE" >/dev/null
docker container inspect "$CONTAINER" >/dev/null 2>&1 && {
  echo "Compatibility container already exists: $CONTAINER" >&2
  exit 1
}

install -d -o 70 -g 70 -m 0700 "$CONFIG_DIR" "$DATA_DIR" "$SPOOL_DIR"
install -o 70 -g 70 -m 0600 "$SOURCE_CONFIG/pgbackrest.conf" "$CONFIG_FILE"

# The generated stanza and repository prefix are unique and deliberately
# outside the pgbackrest directory, so deletion can never target production.
sed -i \
  -e "0,/^\[jobseek\]$/{s/^\[jobseek\]$/[$STANZA]/}" \
  -e 's#^repo1-path=.*#repo1-path='"$REPOSITORY_DIR"'/compatibility/'"$STANZA"'#' \
  -e 's/^pg1-user=.*/pg1-user=postgres/' \
  -e 's/^archive-async=.*/archive-async=n/' \
  -e 's/^process-max=.*/process-max=1/' \
  "$CONFIG_FILE"
grep -Fxq "[$STANZA]" "$CONFIG_FILE"
grep -Fxq "repo1-path=$REPOSITORY_DIR/compatibility/$STANZA" "$CONFIG_FILE"
if grep -Fxq "repo1-path=$REPOSITORY_DIR/pgbackrest" "$CONFIG_FILE"; then
  echo "Refusing compatibility test against the production repository path" >&2
  exit 1
fi

password="$(openssl rand -hex 24)"
docker run --detach \
  --name "$CONTAINER" \
  --memory 1g \
  --restart no \
  --env "POSTGRES_PASSWORD=$password" \
  --env POSTGRES_DB=compatibility \
  --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
  --volume "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
  --volume "$DATA_DIR:/var/lib/postgresql/data" \
  --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
  --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
  "$IMAGE" \
  postgres \
    -c 'archive_mode=on' \
    -c "archive_command=test -f /var/spool/pgbackrest/archive-enabled && pgbackrest --stanza=$STANZA archive-push %p" \
    -c 'archive_timeout=5s' >/dev/null
unset password

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U postgres -d compatibility >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U postgres -d compatibility >/dev/null
docker exec --user postgres "$CONTAINER" pgbackrest --stanza="$STANZA" stanza-create
REPO_CREATED=1
docker exec --user postgres "$CONTAINER" touch /var/spool/pgbackrest/archive-enabled
docker exec --user postgres "$CONTAINER" pgbackrest --stanza="$STANZA" check
docker exec -i "$CONTAINER" psql -U postgres -d compatibility -v ON_ERROR_STOP=1 <<'SQL'
create table restore_probe(id integer primary key, payload text not null);
insert into restore_probe
select id, md5(id::text) from generate_series(1, 1000) as id;
SQL
expected="$(docker exec "$CONTAINER" psql -U postgres -d compatibility -Atc \
  "select md5(string_agg(id::text || ':' || payload, ',' order by id)) from restore_probe")"
test -n "$expected"
docker exec --user postgres "$CONTAINER" pgbackrest --stanza="$STANZA" --type=full backup
docker exec --user postgres "$CONTAINER" pgbackrest --stanza="$STANZA" info >/dev/null

docker stop --time 30 "$CONTAINER" >/dev/null
docker rm "$CONTAINER" >/dev/null
test "$DATA_DIR" = "${BASE_DIR}/${STANZA}/data"
find "$DATA_DIR" -mindepth 1 -delete
docker run --rm \
  --user 70:70 \
  --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
  --volume "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
  --volume "$DATA_DIR:/var/lib/postgresql/data" \
  --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
  --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
  "$IMAGE" \
  pgbackrest --stanza="$STANZA" restore

docker run --detach \
  --name "$CONTAINER" \
  --memory 1g \
  --restart no \
  --volume "$CONFIG_DIR:/etc/jobseek-backup:ro" \
  --volume "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
  --volume "$DATA_DIR:/var/lib/postgresql/data" \
  --volume "$SPOOL_DIR:/var/spool/pgbackrest" \
  --volume "$REPOSITORY_DIR:$REPOSITORY_DIR" \
  "$IMAGE" \
  postgres \
    -c 'archive_mode=on' \
    -c "archive_command=test -f /var/spool/pgbackrest/archive-enabled && pgbackrest --stanza=$STANZA archive-push %p" \
    -c 'archive_timeout=5s' >/dev/null
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U postgres -d compatibility >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
actual="$(docker exec "$CONTAINER" psql -U postgres -d compatibility -Atc \
  "select md5(string_agg(id::text || ':' || payload, ',' order by id)) from restore_probe")"
test "$actual" = "$expected"
docker stop --time 30 "$CONTAINER" >/dev/null
docker rm "$CONTAINER" >/dev/null
delete_compatibility_stanza >/dev/null
REPO_CREATED=0

echo "pgBackRest encrypted repository compatibility backup and restore passed"

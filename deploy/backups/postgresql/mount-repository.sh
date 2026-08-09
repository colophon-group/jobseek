#!/usr/bin/env bash
# Mount the private, encrypted Storage Box share used by pgBackRest.
set -euo pipefail

CONFIG_DIR="${JOBSEEK_POSTGRES_BACKUP_CONFIG:-/etc/jobseek-backup/postgresql}"
MOUNT_DIR="${JOBSEEK_POSTGRES_BACKUP_REPOSITORY:-/mnt/jobseek-postgresql-backups}"
ENV_FILE="$CONFIG_DIR/repository.env"
CREDENTIAL_FILE="$CONFIG_DIR/storage-box.cifs"

test -s "$ENV_FILE"
test -s "$CREDENTIAL_FILE"
[[ "$(stat -c %a "$ENV_FILE")" == 600 ]]
[[ "$(stat -c %a "$CREDENTIAL_FILE")" == 600 ]]
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${JOBSEEK_BACKUP_CIFS_SHARE:?missing JOBSEEK_BACKUP_CIFS_SHARE}"
[[ "$JOBSEEK_BACKUP_CIFS_SHARE" == //*.your-storagebox.de/* ]]

install -d -o 70 -g 70 -m 0700 "$MOUNT_DIR"
if mountpoint -q "$MOUNT_DIR"; then
  [[ "$(findmnt -n -o FSTYPE "$MOUNT_DIR")" == cifs ]]
  [[ "$(findmnt -n -o SOURCE "$MOUNT_DIR")" == "$JOBSEEK_BACKUP_CIFS_SHARE" ]]
else
  mount -t cifs "$JOBSEEK_BACKUP_CIFS_SHARE" "$MOUNT_DIR" \
    -o "seal,vers=3.1.1,hard,cache=strict,noserverino,mfsymlinks,credentials=$CREDENTIAL_FILE,uid=70,gid=70,file_mode=0600,dir_mode=0700"
fi

mountpoint -q "$MOUNT_DIR"
[[ "$(findmnt -n -o FSTYPE "$MOUNT_DIR")" == cifs ]]
[[ "$(findmnt -n -o SOURCE "$MOUNT_DIR")" == "$JOBSEEK_BACKUP_CIFS_SHARE" ]]
mount_options=",$(findmnt -n -o OPTIONS "$MOUNT_DIR"),"
[[ "$mount_options" == *,seal,* ]]
[[ "$mount_options" == *,hard,* ]]
[[ "$mount_options" == *,mfsymlinks,* ]]

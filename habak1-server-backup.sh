#!/usr/bin/env bash

set -euo pipefail

ACTION=$1

if [ "$(hostname)" != "habak1" ]; then
  echo "backup script should only be run on habak1 server."
  exit 1
fi

if [ "${ACTION}" == "prepare" ]; then
  echo Preparing backup...
  if [ "$(id -u)" -ne 0 ]; then
    echo "backup prepare script should be run as root."
    exit 1
  fi
  cd /tmp
  rm -rf backup habak1-backup.tar.gz
  mkdir backup
  cd backup
  tar -czf home.tar.gz /home/habak/* /home/habak/.ssh
  docker exec burn-talkies-postgres-1 pg_dumpall -U burn_talkies_bootstrap | gzip > burn-talkies-db.sql.gz
  docker exec habak-ai-db-1 pg_dumpall -U habak | gzip > habak-ai-db.sql.gz
  docker exec midburn-ops-log-db-1 pg_dumpall -U midburn_ops_log | gzip > midburn-ops-log-db.sql.gz
  tar -czf ai-uploads.tar.gz "$(docker volume inspect habak-ai_uploads --format '{{ .Mountpoint }}')"
  cd ..
  tar -czf habak1-backup.tar.gz backup
  rm -rf backup
  ls -lah /tmp/habak1-backup.tar.gz
  echo "Backup saved to /tmp/habak1-backup.tar.gz"
elif [ "${USER}" != "habak" ]; then
  echo "backup actions except prepare should be run as habak user"
  exit 1
elif [ "${ACTION}" == "copy" ]; then
  if [ "$(sha256sum /tmp/habak1-backup.tar.gz | awk '{print $1}')" != "$(sha256sum /mnt/backup/habak1-backup.tar.gz | awk '{print $1}')" ]; then
    cp -f /tmp/habak1-backup.tar.gz /mnt/backup/habak1-backup.tar.gz
    sync
    if [ "$(sha256sum /tmp/habak1-backup.tar.gz | awk '{print $1}')" != "$(sha256sum /mnt/backup/habak1-backup.tar.gz | awk '{print $1}')" ]; then
      echo "Backup copy failed: checksum mismatch"
      exit 1
    fi
    echo "Backup copied to /mnt/backup/habak1-backup.tar.gz"
  fi
elif [ "${ACTION}" == "upload" ]; then
  LOCAL_HASH=$(gcloud storage hash --format='value(crc32c_hash)' /tmp/habak1-backup.tar.gz)
  REMOTE_HASH=$(gcloud storage hash --format='value(crc32c_hash)' gs://midburn-habak1-server-backups/latest.tar.gz)
  if [ "${LOCAL_HASH}" != "${REMOTE_HASH}" ]; then
    gcloud storage cp /tmp/habak1-backup.tar.gz gs://midburn-habak1-server-backups/latest.tar.gz
    echo "Backup uploaded to GCS latest."
    HOURLY_PATH=$(date +%Y/%m/%d/%H).tar.gz
    if ! gcloud storage ls gs://midburn-habak1-server-backups/hourly/${HOURLY_PATH} >/dev/null 2>&1; then
      gcloud storage cp gs://midburn-habak1-server-backups/latest.tar.gz gs://midburn-habak1-server-backups/hourly/${HOURLY_PATH}
      echo "Backup uploaded to GCS hourly."
    fi
  fi
fi

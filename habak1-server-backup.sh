#!/usr/bin/env bash

set -euo pipefail

ACTION=$1

if [ "${ACTION}" == "prepare" ]; then
  if [ "$(hostname)" != "habak1" ]; then
    echo "backup prepare script should only be run on habak1 server."
    exit 1
  fi
  if [ "$(id -u)" -ne 0 ]; then
    echo "backup prepare script should be run as root."
    exit 1
  fi
  cd /tmp
  rm -rf backup
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
fi

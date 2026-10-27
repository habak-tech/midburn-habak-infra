#!/usr/bin/env bash

set -euo pipefail

ACTION=$1

if [ "$(hostname)" != "habak1" ] &&
   { [ "$ACTION" != "restore" ] || [ "${HABAK_RESTORE_HOST:-}" != "$(hostname)" ]; }; then
  echo "backup script should only be run on habak1 server."
  exit 1
fi

exec 9>/home/habak/.habak1-server-backup-${ACTION}.lock
flock -n 9 || exit 0

if [ "${ACTION}" == "cron" ]; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "backup cron script should be run as root."
    exit 1
  fi
  /home/habak/midburn-habak-infra/habak1-server-backup.sh prepare
  sudo -u habak bash -ic "cd && . .bashrc && cd midburn-habak-infra && ./habak1-server-backup.sh copy && ./habak1-server-backup.sh upload"
elif [ "${ACTION}" == "restore" ]; then
  echo Restoring backup...
  backup_file=${HABAK_BACKUP_FILE:-/tmp/habak1-backup.tar.gz}
  if [ "$(id -u)" -ne 0 ]; then
    echo "backup restore script should be run as root."
    exit 1
  fi
  echo "WARNING: this will overwrite backed-up home files, configuration, databases and AI uploads, then apply .overwrite-after-restore."
  read -r -p "Type RESTORE to restore $backup_file: " confirmation || exit 1
  if [ "$confirmation" != "RESTORE" ]; then
    echo "Restore cancelled."
    exit 1
  fi

  # cron ignores filenames containing a dot. Keep the entry for manual
  # re-enabling after the restored server has been checked.
  if [ -e /etc/cron.d/habak-backup ]; then
    mv -- /etc/cron.d/habak-backup /etc/cron.d/habak-backup.disabled
  fi
  echo "Backup cron disabled. Waiting up to 10 minutes for active backup jobs..."

  # Acquire cron first so its in-flight child actions can finish. All locks
  # share one deadline and remain held for the entire restore.
  lock_deadline=$((SECONDS + 600))
  for action in cron prepare copy upload; do
    exec {lock_fd}>"/home/habak/.habak1-server-backup-${action}.lock"
    lock_remaining=$((lock_deadline - SECONDS))
    if [ "$lock_remaining" -lt 0 ]; then
      lock_remaining=0
    fi
    if ! flock -w "$lock_remaining" "$lock_fd"; then
      echo "Timed out waiting for backup action $action. Backup cron remains disabled; restore aborted." >&2
      exit 1
    fi
    if [[ "$action" == copy || "$action" == upload ]]; then
      chown --reference=/home/habak "/home/habak/.habak1-server-backup-${action}.lock"
    fi
  done

  restore_dir=$(mktemp -d /tmp/habak1-restore.XXXXXX)
  trap 'rm -rf -- "$restore_dir"' EXIT
  payloads=(home.tar.gz ai-uploads.tar.gz burn-talkies-db.sql.gz habak-ai-db.sql.gz midburn-ops-log-db.sql.gz)
  for payload in "${payloads[@]}"; do
    tar -xzf "$backup_file" -C "$restore_dir" "backup/$payload"
    gzip -t "$restore_dir/backup/$payload"
  done
  tar -tzf "$restore_dir/backup/ai-uploads.tar.gz" > "$restore_dir/upload-files"
  # prepare archives absolute paths, which tar stores without the leading /.
  IFS= read -r upload_prefix < "$restore_dir/upload-files"
  if [[ "$upload_prefix" != */habak-ai_uploads/_data/ ]]; then
    echo "Unexpected AI uploads archive layout." >&2
    exit 1
  fi
  while IFS= read -r path; do
    if [[ "$path" != "$upload_prefix"* || "/$path/" == *"/../"* ]]; then
      echo "Unexpected uploads archive path: $path" >&2
      exit 1
    fi
  done < "$restore_dir/upload-files"
  upload_components=${upload_prefix//[^\/]}
  mkdir "$restore_dir/uploads"
  tar -xzf "$restore_dir/backup/ai-uploads.tar.gz" -C "$restore_dir/uploads" \
    --strip-components="$((${#upload_components} - 1))" --numeric-owner

  app_root=${HABAK_RESTORE_APP_ROOT:-/home/habak}
  compose() {
    if [ -n "${HABAK_COMPOSE_WRAPPER:-}" ]; then
      python3 "$HABAK_COMPOSE_WRAPPER" "$@"
    else
      docker compose "$@"
    fi
  }
  projects=(burn-talkies "midburn-habak-ai/Habak AI Agent" midburn-ops-log)
  proxy=midburn-habak-infra/auth-proxy
  script_dir=$(dirname "$(readlink -f "$0")")
  home_restore=${HABAK_HOME_RESTORE_SCRIPT:-$script_dir/restore_home.py}
  if [ ! -f "$home_restore" ]; then
    home_restore="$script_dir/standby/restore_home.py"
  fi
  python3 "$home_restore" stage "$restore_dir/backup/home.tar.gz" "$restore_dir/home" \
    --home-root "$app_root"
  # Validate the complete restored configuration, including local overrides,
  # and fetch published images before stopping the installed deployment.
  for project in "${projects[@]}" "$proxy"; do
    (cd "$restore_dir/home/$project" && compose config --quiet && compose pull --quiet)
  done

  # No services are stopped until every backup payload has been checked.
  trap 'echo "Restore failed; services may be stopped or partially restored. Fix the error and retry." >&2' ERR
  for project in "$proxy" "${projects[@]}"; do
    if [ -f "$app_root/$project/compose.yaml" ] || [ -f "$app_root/$project/compose.production.yaml" ]; then
      (cd "$app_root/$project" && compose down --remove-orphans)
    fi
  done
  python3 "$home_restore" apply "$restore_dir/home" "$app_root"
  if ! docker network inspect midburn-habak-auth-proxy >/dev/null 2>&1; then
    docker network create midburn-habak-auth-proxy
  fi

  db_services=(postgres db db)
  db_users=(burn_talkies_bootstrap habak midburn_ops_log)
  db_dumps=(burn-talkies-db habak-ai-db midburn-ops-log-db)
  for i in "${!projects[@]}"; do
    echo "Restoring ${db_dumps[$i]}..."
    # These pg_dumpall files have no --clean flag. Initialize an empty cluster,
    # bypassing image init scripts and application migrations, before loading.
    # Only the bootstrap CREATE ROLE conflicts with initdb; retain its ALTERs.
    gzip -dc "$restore_dir/backup/${db_dumps[$i]}.sql.gz" |
      sed "0,/^CREATE ROLE ${db_users[$i]};$/ { /^CREATE ROLE ${db_users[$i]};$/d; }" |
      (cd "$app_root/${projects[$i]}" &&
        compose run --rm --no-deps -T --user postgres --entrypoint bash "${db_services[$i]}" -c '
          set -euo pipefail
          if [ "${PGDATA:-}" != /var/lib/postgresql/data ]; then
            echo "Unexpected PGDATA; refusing to clear it." >&2
            exit 1
          fi
          find "$PGDATA" -mindepth 1 -delete
          initdb -D "$PGDATA" -U "$1" --auth-local=trust --auth-host=scram-sha-256 >/dev/null
          # Match the official image entrypoint: applications connect over the
          # Compose network. The temporary restore server stays socket-only.
          echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"
          echo "listen_addresses = '\''*'\''" >> "$PGDATA/postgresql.conf"
          trap '\''pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null'\'' EXIT
          pg_ctl -D "$PGDATA" -o "-c listen_addresses= -c unix_socket_directories=/tmp" -w start >/dev/null
          psql -X -h /tmp -U "$1" -d postgres --set=ON_ERROR_STOP=1 >/dev/null
          # Apply the restored (and optionally overlaid) deployment credentials.
          # SQL identifier/literal quoting is performed by PostgreSQL format().
          set_password() {
            local role=$1 password=$2
            psql -X -h /tmp -U "$restore_user" -d postgres -v ON_ERROR_STOP=1 \
              --set=role="$role" --set=password="$password" >/dev/null <<SQL
SELECT format('\''CREATE ROLE %I LOGIN'\'', :'\''role'\'')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'\''role'\'') \gexec
SELECT format('\''ALTER ROLE %I PASSWORD %L'\'', :'\''role'\'', :'\''password'\'') \gexec
SQL
          }
          restore_user=$1
          set_password "$POSTGRES_USER" "$POSTGRES_PASSWORD"
          psql -X -h /tmp -U "$restore_user" -d postgres -v ON_ERROR_STOP=1 \
            --set=role="$POSTGRES_USER" >/dev/null <<SQL
SELECT format('\''ALTER ROLE %I SUPERUSER'\'', :'\''role'\'') \gexec
SQL
          if [ -n "${DATABASE_OWNER_ROLE:-}" ]; then
            set_password "$DATABASE_OWNER_ROLE" "$DATABASE_OWNER_PASSWORD"
            set_password "$DATABASE_APPLICATION_ROLE" "$DATABASE_APPLICATION_PASSWORD"
          fi
          if [ -n "${MIGRATION_DB_PASSWORD:-}" ]; then
            set_password midburn_migrator "$MIGRATION_DB_PASSWORD"
            set_password midburn_runtime "$RUNTIME_DB_PASSWORD"
          fi
        ' restore "${db_users[$i]}")
  done

  # Let Compose create the named volume; use its current mountpoint even if
  # Docker's data-root differs from the one recorded in the archive.
  (cd "$app_root/${projects[1]}" && compose create --no-build app)
  upload_dir=$(docker volume inspect habak-ai_uploads --format '{{ .Mountpoint }}')
  if [[ -z "$upload_dir" || "$upload_dir" == / || ! -d "$upload_dir" ]]; then
    echo "Invalid AI uploads mountpoint: $upload_dir" >&2
    exit 1
  fi
  find "$upload_dir" -mindepth 1 -delete
  cp -a "$restore_dir/uploads/_data/." "$upload_dir/"

  for project in "${projects[@]}" "$proxy"; do
    (cd "$app_root/$project" && compose up -d --no-build --remove-orphans --wait --wait-timeout 180)
  done
  trap - ERR
  echo "Backup restored successfully."
elif [ "${ACTION}" == "prepare" ]; then
  echo Preparing backup...
  if [ "$(id -u)" -ne 0 ]; then
    echo "backup prepare script should be run as root."
    exit 1
  fi
  cd /tmp
  rm -rf backup habak1-backup-new.tar.gz
  mkdir backup
  cd backup
  tar -czf home.tar.gz -C / \
    --exclude=home/habak/.overwrite-after-restore \
    --exclude='home/habak/.habak1-server-backup-*.lock' home/habak
  docker exec burn-talkies-postgres-1 pg_dumpall -U burn_talkies_bootstrap | gzip > burn-talkies-db.sql.gz
  docker exec habak-ai-db-1 pg_dumpall -U habak | gzip > habak-ai-db.sql.gz
  docker exec midburn-ops-log-db-1 pg_dumpall -U midburn_ops_log | gzip > midburn-ops-log-db.sql.gz
  tar -czf ai-uploads.tar.gz "$(docker volume inspect habak-ai_uploads --format '{{ .Mountpoint }}')"
  cd ..
  tar -czf habak1-backup-new.tar.gz backup
  rm -rf backup
  ls -lah /tmp/habak1-backup-new.tar.gz
  mv -f /tmp/habak1-backup-new.tar.gz /tmp/habak1-backup.tar.gz
  echo "Backup saved to /tmp/habak1-backup.tar.gz"
elif [ "${USER}" != "habak" ]; then
  echo "backup actions except prepare should be run as habak user"
  exit 1
elif [ "${ACTION}" == "copy" ]; then
  if [ "$(sha256sum /tmp/habak1-backup.tar.gz | awk '{print $1}')" != "$(sha256sum /mnt/backup/habak1-backup.tar.gz | awk '{print $1}')" ]; then
    cp -f /tmp/habak1-backup.tar.gz /mnt/backup/habak1-backup-new.tar.gz
    sync
    if [ "$(sha256sum /tmp/habak1-backup.tar.gz | awk '{print $1}')" != "$(sha256sum /mnt/backup/habak1-backup-new.tar.gz | awk '{print $1}')" ]; then
      echo "Backup copy failed: checksum mismatch"
      exit 1
    fi
    cp -f /mnt/backup/habak1-backup.tar.gz /mnt/backup/habak1-backup-old.tar.gz
    sync
    mv -f /mnt/backup/habak1-backup-new.tar.gz /mnt/backup/habak1-backup.tar.gz
    sync
    echo "Backup copied to /mnt/backup/habak1-backup.tar.gz"
  fi
elif [ "${ACTION}" == "upload" ]; then
  HOURLY_PATH=$(date +%Y/%m/%d/%H).tar.gz
  if ! gcloud storage ls gs://midburn-habak1-server-backups/hourly/${HOURLY_PATH} >/dev/null 2>&1; then
    gcloud storage cp /tmp/habak1-backup.tar.gz gs://midburn-habak1-server-backups/hourly/${HOURLY_PATH}
    echo "Backup uploaded to GCS hourly."
  fi
fi

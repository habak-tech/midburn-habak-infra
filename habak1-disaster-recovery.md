# Habak disaster recovery

Install Docker with Compose and Python 3.11+, create the Habak user and its
home at `/home/habak`, and place a trusted backup at `/tmp/habak1-backup.tar.gz`.
Keep `standby/restore_home.py` in the source tree, or install
both scripts together under `/usr/local/lib/habak` as described in the
[standby guide](standby/README.md).

```sh
sudo ./habak1-server-backup.sh restore
```

On a recovery host whose hostname is not `habak1`, explicitly allow that host:

```sh
sudo env HABAK_RESTORE_HOST="$(hostname)" ./habak1-server-backup.sh restore
```

The override permits only restore, not backup creation. `HABAK_BACKUP_FILE`
selects another archive path. `HABAK_RESTORE_APP_ROOT` selects another home
restore destination for recovery rehearsals; its default is `/home/habak`.
`HABAK_HOME_RESTORE_SCRIPT` optionally selects the home helper's installed path.

Type `RESTORE` at the prompt. Recovery overwrites **all home files present in
the backup**, including complete `.env` files, source code, and SSH files.
Repositories are restored directly under `/home/habak`, without an `apps`
subdirectory. Files absent from the archive remain in place.

Before stopping services, the script validates and stages `home.tar.gz`,
validates database and upload payloads, applies the recursive local overwrite
directory, checks the resulting Compose configurations, and pulls images.
It then stops installed stacks, installs the home tree, reinitializes all
three PostgreSQL data directories and loads their dumps, replaces AI uploads,
and starts the applications with health checks. It never builds images.
Restored env values, including any local overrides, set database role passwords.

For a persistent local override, place a file at its relative destination under
`/home/habak/.overwrite-after-restore`. For example:

```text
/home/habak/.overwrite-after-restore/midburn-ops-log/.env
/home/habak/.overwrite-after-restore/midburn-habak-infra/auth-proxy/authelia/session.yml
```

These replace the corresponding restored files, recursively. The overwrite
directory itself and active backup locks survive recovery. Newly created
backups include the home directory's hidden files and exclude the overwrite
directory and lock files. Ownership is mapped to the local home owner for
non-root files, retaining root-owned files and stored access modes.

After confirmation, recovery disables `/etc/cron.d/habak-backup`, then waits
up to ten minutes total for active backup actions to finish. The locks remain
held throughout recovery. Validation or pull failures abort before services
are stopped; failures after installation begins may leave partially restored
files or databases. Correct the error and retry using the installed scripts.
On the standby, automation is installed outside the overwritten home tree.

Backup cron remains disabled after recovery. Re-enable it only on a server
that is supposed to create backups, after verifying its restored state:

```sh
sudo mv /etc/cron.d/habak-backup.disabled /etc/cron.d/habak-backup
```

The standby does not create backups. Its next boot preserves existing files
and data by default; use `sudo habak-standby --restore-next-boot on` to request
one full recovery on the next boot.

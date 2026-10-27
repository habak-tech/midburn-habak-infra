# GCE backup server

The backup VM is `midburn-habak-cloud-backup`, project `midburn-habak`, zone
`me-west1-c`, currently at `35.252.6.198`. Applications serve `/ops/`, `/talkies/`, `/ai/`,
and `/auth/` through Traefik HTTPS with a self-signed certificate. The optional
Cloudflare tunnel uses `habakbak.uumpa.ai`.

Applications and restored home files live directly under `/home/habak`:

```text
/home/habak/
  burn-talkies/
  midburn-ops-log/
  midburn-habak-ai/Habak AI Agent/
  midburn-habak-infra/
  .ssh/
  .overwrite-after-restore/
```

Compose files and image references come from the backup. Startup and recovery
use `--no-build`; images must be available locally or in their registry.
Recovery automation lives outside the restored tree in `/usr/local/lib/habak`,
with state in `/var/lib/habak-standby`.

The backed-up Compose files remain unchanged. The standby's Compose wrapper
adds a runtime policy file outside the home tree: persistent services use
`on-failure:5`, while migration jobs retain `restart: no`. This preserves crash
retries without letting Docker start applications on daemon startup. Only the
boot service starts the stacks, after any requested data recovery finishes.

## Boot behavior

By default, the boot service starts installed applications with their current
files and data. It does not download a backup or apply the overwrite directory.

```sh
sudo habak-standby --restore-next-boot status
sudo habak-standby --restore-next-boot on
sudo habak-standby --restore-next-boot off
```

Arming applies to the next kernel boot only. A service restart in the same boot
does not consume the request. A failed restore retries during that requested
boot; later boots preserve data unless explicitly armed again.
Arming first converts existing Habak containers' `always`/`unless-stopped`
policies to `on-failure:5`. If this fails, the restore request is not armed.

When armed, recovery selects the newest valid hourly object under
`gs://midburn-habak1-server-backups/hourly/**`, downloads it, and checks gzip
integrity. The restore action then:

1. Validates database and upload payloads and stages the **complete**
   `backup/home.tar.gz` tree with `/home/habak` as its destination.
2. Applies the local overwrite directory recursively to the staged files.
3. Validates the resulting Compose configurations and pulls their images.
4. Stops the installed stacks, overwrites home files with the staged result,
   restores all three databases and AI uploads, and starts the applications.
5. Starts the Cloudflare tunnel only when its restored configuration has a token.

Home recovery includes source files, `.env` files, SSH configuration, hidden
files present in the archive, and other backed-up files. There is no env-variable
merge or implicit preservation of local credentials. A local file survives at a
backed-up path only if supplied by the overwrite directory. Unrelated files
absent from the archive remain in place. Archive file modes are retained;
non-root ownership is mapped to the local Habak home owner. Root-owned files
remain root-owned. Paths and links escaping the home tree and special files
are rejected before services stop.

On an armed boot, application containers, proxy, and tunnel remain stopped
while recovery downloads and restores the backup. Only the temporary PostgreSQL
containers needed to load dumps run during database restoration. Application
Compose startup happens after all dumps and uploads have been restored.
A failed download, validation, image pull, or data restore leaves applications
stopped on that boot; it does not fall back to serving the old deployment.
During a manual restore on a running server, preflight failures preserve its
existing files and running services.
Once installation begins, a failure can leave partially restored files or data;
inspect the journal and retry after correcting the error.

## Recursive overwrite directory

Place overrides under `/home/habak/.overwrite-after-restore` at the same relative
path as their final destination. For example:

```text
.overwrite-after-restore/midburn-ops-log/.env
  -> /home/habak/midburn-ops-log/.env

.overwrite-after-restore/midburn-habak-infra/auth-proxy/authelia/session.yml
  -> /home/habak/midburn-habak-infra/auth-proxy/authelia/session.yml
```

A supplied `.env` replaces the entire backed-up `.env`. Nested directories and
hidden files are supported. The overwrite directory itself is preserved during
recovery, ignored if present in an older backup, and excluded from newly created
home backups. Active backup lock files are also preserved.

```sh
sudo install -d -o habak -g habak -m 700 \
  /home/habak/.overwrite-after-restore/midburn-ops-log
sudo cp /home/habak/midburn-ops-log/.env \
  /home/habak/.overwrite-after-restore/midburn-ops-log/.env
sudo chmod 600 /home/habak/.overwrite-after-restore/midburn-ops-log/.env
sudoedit /home/habak/.overwrite-after-restore/midburn-ops-log/.env
```

The installed overlay is seeded with the current four application/proxy env
files and local Authelia configuration, secrets, users, and a consistent SQLite
snapshot. This retains the backup server's passwords, volume names, IP session
settings, and tunnel token through explicit overrides. Maintain these overlay
copies when changing settings that must survive recovery. Preserve the Authelia
SQLite database together with its storage encryption key, or supply a fresh
compatible database when changing that key. Edit the live destination separately
when applying a setting immediately; overlay files take effect during recovery.

## Cloudflare tunnel

Set `CLOUDFLARE_TUNNEL_TOKEN` in both the live and persistent override env files:

```text
/home/habak/midburn-habak-infra/auth-proxy/.env
/home/habak/.overwrite-after-restore/midburn-habak-infra/auth-proxy/.env
```

Then restart `habak-standby-restore.service` to apply the live configuration
without restoring data. Configure the tunnel origin as `https://traefik:443`
with origin TLS verification disabled for the self-signed certificate.

## Install the automation

Install Docker Engine with Compose, Python 3.11+, and GCloud. The instance
service account needs permission to list and read the backup bucket. Create
the Habak user and `/home/habak`; normal startup requires the applications to
be installed, while a full recovery can create them from the backup.

```sh
sudo install -d -m 755 /usr/local/lib/habak
sudo install -m 755 standby/restore_latest.py /usr/local/lib/habak/restore_latest.py
sudo install -m 755 standby/restore_home.py /usr/local/lib/habak/restore_home.py
sudo install -m 755 standby/compose_guard.py /usr/local/lib/habak/compose_guard.py
sudo install -m 755 standby/habak-standby /usr/local/sbin/habak-standby
sudo install -m 755 habak1-server-backup.sh /usr/local/lib/habak/habak1-server-backup.sh
sudo install -m 644 standby/habak-standby-restore.service /etc/systemd/system/
sudo install -d -o habak -g habak -m 700 /home/habak/.overwrite-after-restore
sudo python3 /usr/local/lib/habak/compose_guard.py --protect-existing
sudo systemctl daemon-reload
sudo systemctl enable habak-standby-restore.service
```

The VM has a 50 GiB boot disk and uses Docker's classic image store and bounded
container logs. Keep space for downloaded archives, the staged home tree, and
images. A static external IP is required if the same direct-IP URL must survive
GCE stop/start. Cloud firewall configuration must permit the desired ports.

## Operations and verification

```sh
sudo systemctl status habak-standby-restore.service
sudo journalctl -u habak-standby-restore.service -b --no-pager
sudo cat /var/lib/habak-standby/last-success.json
sudo cat /var/lib/habak-standby/last-boot.json
sudo habak-standby --restore-next-boot status
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

`last-success.json` records the last full recovery's backup URI, SHA256, and boot
ID. `last-boot.json` records startup with mode `existing` or `restored`.
Restarting the boot service applies installed configuration while preserving
data. To run a full recovery, arm the next boot and reboot the instance.
The unit's shutdown hook also protects existing Habak containers before Docker
stops. After Docker maintenance, restart the standby service to bring the
applications up. For manual Compose changes on this VM, use
`sudo python3 /usr/local/lib/habak/compose_guard.py ...` in the project directory
so newly created containers retain the boot guard. Installing the guard and
running `--protect-existing` once is required before the next reboot.
The standalone restore action and its confirmation prompt are described in
[the recovery guide](../habak1-disaster-recovery.md).

The independent password-protected [Cloudflare start Worker](start-worker/README.md)
can start a stopped VM. It does not arm recovery or change its boot setting.

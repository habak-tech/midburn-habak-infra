# Habak test VM on ori-lp

The `habak-test` libvirt VM runs the complete stack at
<https://192.168.50.218:8543/ops/>. Other fixed paths are `/talkies/`, `/ai/`,
and `/auth/`. The root redirects to Ops. No per-app hostname or path settings
are needed.

## VM and access

- Host SSH alias: `ori-lp`; libvirt connection: `qemu:///system`.
- Guest: Debian 13 amd64, UEFI, 4 vCPUs, 8 GiB RAM, 50 GiB sparse disk.
- Disk directory on host: `/var/lib/libvirt/images/habak-test/`.
- Libvirt NAT network: `default`; reserved IP `192.168.122.60` for MAC
  `52:54:00:ab:ac:60`. The guest keeps this private IP; LAN clients use the host address below.
- Guest SSH user: `habak`, authenticated with the container's SSH key.

```sh
ssh -J ori-lp habak@192.168.122.60
ssh ori-lp 'virsh -c qemu:///system list --all'
ssh ori-lp 'virsh -c qemu:///system start habak-test'
ssh ori-lp 'virsh -c qemu:///system shutdown habak-test'
```

Use UEFI when recreating the VM: the downloaded Debian 13 cloud image looped at
the bootloader under legacy BIOS on this host. The cloud-image SHA512 checksum
was verified before provisioning. The guest has Docker Engine, Compose,
cloud-init, and the QEMU guest agent.

## Application deployment

Source snapshots are under `/home/habak/apps/`. They include the local fixed-path
changes; they are not Git clones and do not auto-update from GitHub.

| Directory | Compose project | Images built locally |
| --- | --- | --- |
| `midburn-habak-infra/auth-proxy` | `auth-proxy` | upstream Traefik and Authelia |
| `midburn-ops-log` | `habak-ops` | `habak-ops:vm`, `habak-ops-postgres:vm` |
| `burn-talkies` | `habak-talkies` | `habak-talkies:vm`, `habak-talkies-postgres:vm` |
| `midburn-habak-ai/Habak AI Agent` | `habak-ai` | `habak-ai:vm`, `habak-ai-migrate:vm` |

Run `/home/habak/build.sh` inside the guest to rebuild all application images
from the deployed source. Build logs are in `/home/habak/build-logs/`. After
updating source and rebuilding, run `docker compose up -d --wait` in each
application directory. Its `.env` selects the production Compose files and
local image names. Do not replace those env files with repository examples.

All three databases use fresh persistent Docker volumes. AI uploads and
Authelia's SQLite database are persistent. Only proxy ports 8181 and 8543 are
published; app/database ports stay private to Docker. Containers restart with
Docker when the VM boots. Migrations are one-shot jobs and normally show Exited
with exit code 0.

The local TLS certificate and key live in `auth-proxy/tls/`. The VM-only
`compose.tls.yaml` enables the file provider and loads `dynamic/tls.yml`. The
certificate has both the LAN address and VM IP as its SAN and remains stable across proxy restarts.
It is self-signed; accept it in the test browser. Other clients need their own
certificate trust decision. The generated certificate expires after one year.

## Credentials and test data

Fresh database passwords and Authelia secrets live only in guest env/secret
files. AI provider keys were obtained from the existing server backup; no
production database or uploads were restored. Do not commit any env files,
keys, cookies, or login credentials.

The test administrator is `admin`. Its password is recorded in the guest's
mode-0600 `/home/habak/apps/test-login.json` and on the host in
`~/.local/state/habak-vm/login.json`.

The VM includes one clearly labeled test document and one test event used to
verify real ingestion and the Ops AI iframe. They are test fixtures, not
operational instructions.

## Verification

From the source checkout, with sibling repositories present:

```sh
python3 tests/test_path_routing.py
```

Inside the VM:

```sh
cd /home/habak/apps/midburn-habak-infra
python3 tests/vm_smoke.py \
  --origin https://192.168.50.218:8543 \
  --login-file /home/habak/apps/test-login.json \
  --ca-file auth-proxy/tls/server.crt

docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

The smoke test verifies anonymous denial, spoofed identity rejection, path
boundaries, redirects, real login, app pages/assets, database APIs, Talkies SSE,
query strings, and deep links. It does not call model providers. Browser
acceptance additionally checks navigation, real document ingestion, streamed AI
answers, and the Ops AI iframe.

## LAN access

The canonical public origin is `https://192.168.50.218:8543`, using ori-lp's
Wi-Fi address. Systemd socket proxies forward host TCP ports 8181 and 8543 to
the same ports on `192.168.122.60`. This leaves the host Wi-Fi configuration
and VM NAT network intact. No application router checks the Host header;
Authelia's gitignored `auth-proxy/authelia/session.yml` lists cookie settings
for both `https://192.168.50.218:8543` and `https://192.168.122.60:8543`.
The shared configuration contains no deployment hostname variables. Ops uses relative links; Talkies and AI top links use the LAN origin.

The installed units are tracked in `vm/host/habak-lan@.socket` and
`vm/host/habak-lan@.service`. Both socket instances are enabled at boot.

```sh
ssh ori-lp 'systemctl status habak-lan@8543.socket habak-lan@8543.service'
ssh ori-lp 'systemctl status habak-lan@8181.socket habak-lan@8181.service'
```

Reserve `192.168.50.218` for ori-lp in the router's DHCP settings so the address
stays stable. This is a host port forward, not a separate LAN address assigned
to the guest. The laptop and VM must be running. TCP forwarding does not
preserve individual LAN client IPs in the application logs.

To remove LAN access, stop and disable both socket instances and stop their
services. To restore the private origin as well, restore each app's
`.env.before-lan`, restore the TLS `.before-lan` files, and recreate affected
containers. No database restore is needed.

```sh
ssh ori-lp 'sudo systemctl disable --now habak-lan@8181.socket habak-lan@8543.socket'
ssh ori-lp 'sudo systemctl stop habak-lan@8181.service habak-lan@8543.service'
```

## Regular-user testing

`volunteer` belongs only to `midburn-ops-user`, with no administrator group.
Its generated password is stored in the guest at
`/home/habak/apps/volunteer-login.json` and on ori-lp at
`~/.local/state/habak-vm/volunteer-login.json`, both mode 0600.
Ops reports role `user`, AI reports `volunteer`, and Talkies reports `operator`.
Ops menu defaults include both Talkies (`/talkies/`) and AI (`/ai/`).

# Auth proxy

All applications share one HTTPS origin and fixed paths:

| Service | Path |
| --- | --- |
| Operations log | `/ops/` |
| Burn Talkies | `/talkies/` |
| Habak AI | `/ai/` |
| Authelia | `/auth/` |

Unmatched paths (including `/`) redirect to `CATCH_ALL_REDIRECT_URL`, which
defaults to `/ops/`. Use a path to preserve the current hostname/IP and port,
or an absolute URL to redirect elsewhere. The fallback router has priority `1`,
so application and auth routes take precedence. App-generated 404 responses
are not redirected. Choose a destination handled by an existing route to avoid
a redirect loop. After changing `.env`, run `docker compose up -d traefik`
to apply it. Redirects are temporary (302).

Application paths are built into the application code; there are
no path environment variables, hostname-based application routers, or path
Compose overrides. Rebuild application images after upgrading from root-path
builds.

## Setup

Install Docker Engine with Compose. Keep `midburn-habak-infra`,
`midburn-ops-log`, `burn-talkies`, and `midburn-habak-ai` in sibling directories.

1. Copy `.env.example` to `.env` for listener ports (defaults: HTTP 8181,
   HTTPS 8543). Copy `authelia/session.yml.example` to `authelia/session.yml`.
   Set its complete `session.cookies` list to the hosts/IPs clients will use,
   with each origin's `/auth` login URL and `/ops/` default redirect URL.
   The deployment-specific `session.yml` is gitignored. Authelia loads it after
   the shared `configuration.yml`; keep cookie entries only in `session.yml`.
2. Generate the three Authelia secrets as described in
   [authelia/secrets.md](authelia/secrets.md). Copy
   `authelia/users.yml.example` to `authelia/users.yml` and set a password hash.
   Give users `midburn-ops-user` or `midburn-ops-admin` membership.
3. Configure each application's database credentials from its example env file.
   Build images from the updated source. Ops uses
   `COMPOSE_FILE=compose.yaml:compose.prod.yaml`; Talkies uses
   `COMPOSE_FILE=compose.production.yaml`; AI uses `compose.yaml`.
4. Create the shared network and start the proxy:

   ```sh
   docker network inspect midburn-habak-auth-proxy >/dev/null 2>&1 || \
     docker network create midburn-habak-auth-proxy
   docker compose up -d --wait
   ```

5. Start each app with `docker compose up -d --wait` from its own directory
   (`Habak AI Agent/` for AI). Migrations run before application startup.

Open `https://<your-host>:8543/ops/`. HTTPS is required for the session cookie.
The one-off `tls-init` container generates a self-signed certificate in the
`traefik-certs` named volume before Traefik starts. It installs OpenSSL from
Alpine's package repository on first generation, which requires internet access.
Traefik mounts the volume read-only and reuses the certificate across restarts,
container recreation, and `docker compose down` / `up`. `docker compose down -v`
deletes the certificate volume. Back up this volume separately when migrating
hosts; the existing server backup script does not include it.

Set `TLS_CERT_SAN` in `.env` to the DNS names/IPs clients use before the first
start (for example `DNS:habak.local,IP:192.168.1.10`). It defaults to localhost.
The certificate lasts one year and is not automatically renewed. Changing
`TLS_CERT_SAN` does not replace an existing certificate. To regenerate it, stop
Traefik, remove `server.crt` and `server.key` from this volume, then run
`docker compose up -d traefik`. Clients must trust the replacement again.
Accept the self-signed certificate for local testing or configure a certificate
trusted by clients for persistent deployments. Applying this setup replaces
Traefik's previous in-memory certificate on the first deployment.

The proxy clears client-supplied identity headers before authentication.
Authentication runs before stripping `/ops` or `/talkies`. AI and Authelia
receive their original paths. Databases and production application ports are
not published to the host.

Ops defaults to `/ai/embed` for embedded chat and `/auth/logout` for logout.
Talkies and AI top-link settings accept absolute URLs; use the same HTTPS
origin and fixed paths for cross-app links.

## Persistent logins

Authelia's expiration, inactivity, and remember-me timers are all `30d`
(2,592,000 seconds). Select **Remember me** when logging in to retain the
cookie after closing the browser. Without it, server-side sessions still
survive restarts, but the browser may discard its session cookie on exit.
Logging out or clearing cookies ends access before the 30 days have elapsed.
Deployment-specific `authelia/session.yml` entries inherit these timers;
remove any shorter per-domain timer overrides when upgrading.

Sessions are encrypted using the existing `SESSION_SECRET` and stored in
`session-redis`. Redis persists them in the `authelia-sessions` named volume
with append-only logging and `appendfsync always`, flushing writes before
acknowledging them. This trades some write latency for durability across
abrupt shutdowns. Redis has no published port and is attached only to the
private, internal `sessions` network shared with Authelia. Both services
restart automatically with Docker; Docker must be enabled at server boot.
Authelia waits for Redis health during Compose startup.

To upgrade an existing deployment, keep its `session.yml`, user database,
and secrets, then run from `auth-proxy/`:

```sh
docker compose pull authelia session-redis
docker compose run --rm --no-deps authelia authelia validate-config \
  --config /config/configuration.yml --config /config/session.yml
docker compose up -d --wait authelia session-redis
```

The switch from memory to Redis requires **one fresh login**. Subsequent
Authelia/Redis container restarts, recreation, and server reboots retain
sessions as long as the volume and session secret remain intact. Normal
user and admin logins continue through Authelia; no automatic identity is
injected.

Do not use `docker compose down -v` when retaining logins: it deletes the
session volume (and TLS volume). The existing server backup script does not
archive this new named volume; recovery onto a different server requires
fresh logins. This does not affect ordinary restarts on the same server.
Rotating `SESSION_SECRET` also invalidates the stored sessions.

To roll back, restore the previous Compose and Authelia configuration and
recreate Authelia. Users must log in again with the in-memory provider.
Leave the Redis volume intact until the rollback is confirmed.

The isolated persistence check creates synthetic users/secrets and cleans
up only its own containers and volumes:

```sh
python3 tests/session_persistence_smoke.py
# If Docker runs outside this environment, use its reachable host address:
python3 tests/session_persistence_smoke.py --host <docker-host-IP>
```

It checks concurrent sessions, actual 30-day Redis TTLs and remembered cookie
expiry, Authelia restart, full container recreation, abrupt process termination,
and logout isolation/persistence. It does not reboot the Docker host or run
for 30 days.

## Optional Cloudflare tunnel

Set `CLOUDFLARE_TUNNEL_TOKEN` and `COMPOSE_PROFILES=cloudflare`. Configure one
public hostname pointing to `https://traefik:443`, with origin certificate
verification disabled only when using Traefik's self-signed certificate.
Add that hostname and its HTTPS URLs to `authelia/session.yml`, and set
`HTTPS_PORT=443` so the HTTP redirect uses the public port. All application paths use the same tunnel hostname.

## Verification

From the infrastructure repository:

```sh
python3 tests/test_path_routing.py
```

After building the sibling applications, run `python3 tests/path_runtime_smoke.py`
for prefixed pages, assets, deep links, and AI authorization checks. On the
running deployment also verify login, database APIs, Talkies SSE, the Ops AI
iframe, and logout. Configuration checks alone do not exercise those flows.

After editing session origins, validate both files and restart Authelia:

```sh
docker compose exec authelia authelia validate-config --config /config/configuration.yml --config /config/session.yml
docker compose restart authelia
```

Each origin needs network access and a matching TLS certificate. Unrelated
hosts/IPs have separate login sessions. App routing remains path-based.

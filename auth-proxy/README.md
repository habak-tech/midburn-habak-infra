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
Traefik's default certificate is self-signed; accept it for local testing or
configure a certificate trusted by clients for persistent deployments.

The proxy clears client-supplied identity headers before authentication.
Authentication runs before stripping `/ops` or `/talkies`. AI and Authelia
receive their original paths. Databases and production application ports are
not published to the host.

Ops defaults to `/ai/embed` for embedded chat and `/auth/logout` for logout.
Talkies and AI top-link settings accept absolute URLs; use the same HTTPS
origin and fixed paths for cross-app links.

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

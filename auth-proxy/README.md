# Auth Proxy

## Setup

See authelia/secrets.md for instructions on generating secrets.

Copy users.yml.example to users.yml, generate password hash using `docker run -it --entrypoint authelia authelia/authelia crypto hash generate`

Copy .env.example to .env and edit the values as needed.

Set in /etc/hosts - on the server with 127.0.0.1 and on local machines with the server IP:

```
127.0.0.1 auth.habak.midburn ops.habak.midburn ai.habak.midburn talkies.habak.midburn
```

Setup shared network:

```
docker network create midburn-habak-auth-proxy
```

Setup midburn ops log

```
cd ../midburn-ops-log
cp .env.example .env
# modify for production
echo "
APP_IMAGE=ghcr.io/habak-tech/midburn-ops-log:latest
POSTGRES_IMAGE=ghcr.io/habak-tech/midburn-ops-log-postgres:latest
" >> .env
```

Setup burn talkies

```
cd ../burn-talkies
cp .env.example .env
# modify for production - replace the secrets
```

setup habak ai

```
cd ../midburn-habak-ai/Habak\ AI\ Agent/
cp .env.production.example .env
# modify for production
```

## Running

```
cd ../midburn-ops-log
docker compose up -d

cd ../burn-talkies
docker compose up -d

cd ../midburn-habak-ai/Habak\ AI\ Agent/
docker compose up -d

cd ../midburn-habak-infra/auth-proxy
docker compose up -d
```

Login:

```
https://ops.habak.midburn
```

## Cloudflare Tunnel

The optional `cloudflare` profile runs a remotely managed Cloudflare Tunnel.
Create the tunnel in Cloudflare Zero Trust, then configure public hostnames for
the Authelia portal and protected application. Set each hostname's service to
`https://traefik:443` and enable **No TLS Verify** in its origin settings because
Traefik uses its internal default certificate on the Docker network.

Set the public hostnames and tunnel token in `.env`:

```env
AUTH_HOSTNAME=auth.example.org
APP_HOSTNAME=ops.example.org
COOKIE_DOMAIN=example.org
AUTH_PORTAL_URL=https://auth.example.org
CLOUDFLARE_TUNNEL_TOKEN=replace-with-the-tunnel-token
```

The protected application's Traefik router must use the same `APP_HOSTNAME`.
The application has a separate `.env`, so in
`../midburn-ops-log/compose.prod.yaml` replace its router rule with:

```yaml
- traefik.http.routers.midburn-ops-log.rule=Host(`${APP_HOSTNAME:-ops.habak.midburn}`)
```

Then set the same public hostname in `../midburn-ops-log/.env`:

```env
APP_HOSTNAME=ops.example.org
```

Treat the tunnel token as a secret; `.env` is excluded from Git. Users with
access to the Docker daemon can inspect container environment variables and
therefore access the token.

Add to .env:

```
COMPOSE_PROFILES=cloudflare
```

Start the proxy normally

## Shared IP / path routing

Path mode serves all three apps and the login portal from one
HTTPS origin, without DNS or `/etc/hosts` entries:

| Component | Example URL |
| --- | --- |
| Operations log | `https://192.0.2.10:8543/ops/` |
| Burn Talkies | `https://192.0.2.10:8543/talkies/` |
| Habak AI | `https://192.0.2.10:8543/ai/` |
| Authelia login | `https://192.0.2.10:8543/auth/` |

Replace `192.0.2.10` with the server IP. In this directory's `.env`, keep the
existing port and secret configuration and add:

```env
PUBLIC_HOST=192.0.2.10
```

The override sets Authelia's public host and cookie scope to `PUBLIC_HOST`, its
server path to `/auth`, and its portal URL to
`https://PUBLIC_HOST:HTTPS_PORT/auth`. The portal bypass rule applies only to
`/auth` and its descendants; protected apps on the same IP still require a
user in `midburn-ops-user` or `midburn-ops-admin`. HTTPS is required for the
session cookie. Use a certificate trusted by your clients with an IP SAN for
this IP; Traefik's default self-signed certificate is suitable only for manual
testing after accepting the browser certificate prompt.

Create the shared network if it does not already exist, then start the proxy:

```sh
docker network inspect midburn-habak-auth-proxy >/dev/null 2>&1 || \
  docker network create midburn-habak-auth-proxy
docker compose up -d --wait
```

From each application directory, build `web` (Talkies) or `app` (Ops/AI), then
run `docker compose up -d --wait`. Keep existing database and migration images;
only the application browser bundles require prefix-specific builds. The Ops
production override still uses its configured PostgreSQL image.

Traefik routes match the exact IP/host and either the prefix itself or the
prefix followed by `/`; `/ops-other` cannot match `/ops`. Authentication runs
before prefix stripping for Ops and Talkies. AI and Authelia receive the
original prefixed URL and serve their own subpaths. Container health checks
remain internal, with the AI check explicitly using its Next.js base path.

For cross-app navigation, configure link destinations explicitly. In Ops use
`AI_URL=https://192.0.2.10:8543/ai/embed` and
`LOGOUT_URL=https://192.0.2.10:8543/auth/logout`; keep the same port as the proxy.

Smoke-check login, a deep-link reload, browser assets, API calls, Talkies SSE,
and the Ops AI iframe on the target host. To return to domain/root-path mode,
remove `compose.path.yaml` from `COMPOSE_FILE`, clear `APP_BASE_PATH`, select the
normal image tags, and rebuild any locally built root-path application images.

### Validate changes locally

From `midburn-habak-infra`, validate merged Compose configuration without
starting the production stacks:

```sh
python3 tests/test_path_routing.py
```

After installing each app's dependencies and building Talkies with
`APP_BASE_PATH=/talkies`, Ops with `APP_BASE_PATH=/ops`, and AI with
`APP_BASE_PATH=/ai` (plus its documented build database URL), run:

```sh
python3 tests/path_runtime_smoke.py
```

The smoke script uses temporary loopback ports and stops all its application
processes. It checks prefixed pages, JS/CSS assets, Vite deep links, and AI
anonymous/admin authorization boundaries and redirects. It does not contact
model providers or use a real database, and does not replace target-host
login/API/SSE acceptance checks.

The prefix build requirement follows [Next.js basePath behavior](https://nextjs.org/docs/pages/api-reference/config/next-config-js/basePath).
Authelia's portal subpath uses its [server address setting](https://www.authelia.com/configuration/miscellaneous/server/).

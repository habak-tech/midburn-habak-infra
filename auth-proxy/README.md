# Auth Proxy

## Setup

See authelia/secrets.md for instructions on generating secrets.

Copy users.yml.example to users.yml

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
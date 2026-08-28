# Auth Proxy

## Setup

See authelia/secrets.md for instructions on generating secrets.

Copy users.yml.example to users.yml

Copy .env.example to .env and edit the values as needed.

Set in /etc/hosts:

```
127.0.0.1 auth.habak.midburn ops.habak.midburn
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

## Running

```
cd ../midburn-ops-log
docker compose -f compose.yaml -f compose.prod.yaml up -d

cd ../midburn-habak-infra/auth-proxy
docker compose up -d
```
